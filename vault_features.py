"""Recovery, health and reading features. Originals are never modified here."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from collection_tree import collection_rows


def stamp(): return datetime.now(timezone.utc).isoformat()
def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


class Features:
    def init_features(self):
        with self.db() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS trash(id TEXT PRIMARY KEY,created_at TEXT,label TEXT,payload TEXT);
                CREATE TABLE IF NOT EXISTS trash_refs(batch_id TEXT REFERENCES trash(id) ON DELETE CASCADE,sha TEXT REFERENCES objects(sha),PRIMARY KEY(batch_id,sha));
                CREATE TABLE IF NOT EXISTS reading(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,page INTEGER DEFAULT 1,updated_at TEXT);
                CREATE TABLE IF NOT EXISTS saved_searches(id TEXT PRIMARY KEY,name TEXT UNIQUE,filters TEXT,created_at TEXT);
                CREATE TABLE IF NOT EXISTS scan_state(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,size INTEGER,mtime_ns INTEGER);
                CREATE TABLE IF NOT EXISTS ocr(sha TEXT PRIMARY KEY REFERENCES objects(sha) ON DELETE CASCADE,chunks TEXT,updated_at TEXT);
            ''')

    def remove_files(self, ids): return self.trash_files(ids)
    def remove_source(self, sid): return self.trash_source(sid)

    def trash_files(self, ids, source_id=None):
        ids = self._checked_ids(ids) if ids and source_id is None else list(ids)
        with self.lock, self.db() as c:
            if source_id:
                files=[dict(r) for r in c.execute('SELECT * FROM files WHERE source_id=?',(source_id,))]
            else:
                files = [dict(r) for r in c.execute('SELECT * FROM files' + (' WHERE id IN (' + ','.join('?' for _ in ids) + ')' if ids else ' WHERE 0'), ids)]
            sources = {r['source_id'] for r in files}
            if source_id: sources.add(source_id)
            source_rows = [dict(r) for r in c.execute('SELECT * FROM sources') if r['id'] in sources]
            if not files and not source_rows: raise ValueError('资料不存在')
            fids = {r['id'] for r in files}
            revisions = [dict(r) for r in c.execute('SELECT * FROM revisions') if r['file_id'] in fids]
            memberships = [dict(r) for r in c.execute('SELECT cf.*,c.name FROM collection_files cf JOIN collections c ON c.id=cf.collection_id') if r['file_id'] in fids]
            tree={row['id']:row for row in collection_rows(c)}
            for membership in memberships: membership['path']=tree[membership['collection_id']]['path']
            payload = {'files': files, 'sources': source_rows, 'revisions': revisions, 'memberships': memberships, 'reading': [dict(r) for r in c.execute('SELECT * FROM reading') if r['file_id'] in fids]}
            payload['notes']=[dict(r) for r in c.execute('SELECT * FROM file_notes') if r['file_id'] in fids]
            bid = uuid.uuid4().hex
            label = source_rows[0]['name'] if source_id else files[0]['name'] if len(files) == 1 else f'{len(files)} 份资料'
            c.execute('INSERT INTO trash VALUES(?,?,?,?)', (bid, stamp(), label, json.dumps(payload, ensure_ascii=False)))
            shas = {r['sha'] for r in files} | {r['sha'] for r in revisions}
            c.executemany('INSERT INTO trash_refs VALUES(?,?)', [(bid, s) for s in shas])
            for fid in fids:
                c.execute('DELETE FROM search WHERE file_id=?', (fid,))
                c.execute('DELETE FROM revisions WHERE file_id=?', (fid,))
                c.execute('DELETE FROM files WHERE id=?', (fid,))
            for sid in sources:
                if sid == source_id: c.execute('DELETE FROM sources WHERE id=?', (sid,))
                else: c.execute("DELETE FROM sources WHERE id=? AND kind='file' AND NOT EXISTS(SELECT 1 FROM files WHERE source_id=?)", (sid, sid))
        self.manifest()
        return {'files': len(files), 'trash_id': bid, 'objects': 0, 'bytes': 0}

    def trash_source(self, sid):
        with self.lock, self.db() as c:
            if not c.execute('SELECT 1 FROM sources WHERE id=?', (sid,)).fetchone(): raise ValueError('来源不存在')
            for row in c.execute("SELECT id,payload FROM jobs WHERE state IN ('queued','running')").fetchall():
                if sid in (row['payload'] or ''): c.execute('DELETE FROM jobs WHERE id=?', (row['id'],))
            ids = [r[0] for r in c.execute('SELECT id FROM files WHERE source_id=?', (sid,))]
        return self.trash_files(ids, source_id=sid)

    def trash_list(self):
        with self.db() as c:
            return [{'id': r['id'], 'created_at': r['created_at'], 'label': r['label'], 'files': len(json.loads(r['payload'])['files'])} for r in c.execute('SELECT * FROM trash ORDER BY created_at DESC')]

    def restore_trash(self, bid):
        with self.lock, self.db() as c:
            row = c.execute('SELECT payload FROM trash WHERE id=?', (bid,)).fetchone()
            if not row: raise ValueError('回收记录不存在')
            p = json.loads(row[0])
            # Never overwrite something the user imported again after deletion.
            for f in p['files']:
                if c.execute('SELECT 1 FROM files WHERE id=? OR (source_id=? AND origin_path=?)', (f['id'], f['source_id'], f['origin_path'])).fetchone():
                    raise ValueError('资料已重新导入，恢复会产生冲突；现有资料未改变')
            for s in p['sources']:
                existing = c.execute('SELECT * FROM sources WHERE id=?', (s['id'],)).fetchone()
                if existing and (existing['root'] != s['root'] or existing['mode'] != s['mode']): raise ValueError('来源已变化，请先处理来源冲突')
                c.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)', tuple(s[k] for k in ('id','name','root','mode','kind','created_at','last_scan')))
            for f in p['files']:
                cols = ('id','sha','source_id','name','origin_path','kind','tags','starred','added_at','updated_at')
                c.execute('INSERT INTO files(' + ','.join(cols) + ') VALUES(' + ','.join('?' for _ in cols) + ')', [f[k] for k in cols])
                self._index(c, f['id'])
            for n in p.get('notes', []):
                c.execute('INSERT OR REPLACE INTO file_notes VALUES(?,?,?)',(n['file_id'],n['body'],n['updated_at']))
            for r in p.get('reading', []): c.execute('INSERT OR REPLACE INTO reading VALUES(?,?,?)', (r['file_id'],r['page'],r['updated_at']))
            for r in p['revisions']: c.execute('INSERT INTO revisions VALUES(?,?,?)', (r['file_id'], r['sha'], r['changed_at']))
            for m in p['memberships']:
                col = c.execute('SELECT id FROM collections WHERE id=?',(m['collection_id'],)).fetchone()
                if col:
                    cid=col[0]
                else:
                    cid,_=self._ensure_collection_path(c,m.get('path',[m['name']]))
                c.execute('INSERT OR IGNORE INTO collection_files VALUES(?,?,?)', (cid, m['file_id'], m['added_at']))
            c.execute('DELETE FROM trash WHERE id=?', (bid,))
        self.manifest()
        return {'files': len(p['files'])}

    def purge_trash(self, bid):
        with self.lock, self.db() as c:
            if not c.execute('SELECT 1 FROM trash WHERE id=?', (bid,)).fetchone(): raise ValueError('回收记录不存在')
            c.execute('DELETE FROM trash WHERE id=?', (bid,))
            orphan = [dict(r) for r in c.execute('''SELECT o.sha,o.vault_path,m.thumb_path FROM objects o LEFT JOIN media m ON m.sha=o.sha
                WHERE NOT EXISTS(SELECT 1 FROM files f WHERE f.sha=o.sha)
                  AND NOT EXISTS(SELECT 1 FROM revisions r WHERE r.sha=o.sha)
                  AND NOT EXISTS(SELECT 1 FROM trash_refs t WHERE t.sha=o.sha)''')]
            for r in orphan:
                c.execute('DELETE FROM media WHERE sha=?', (r['sha'],))
                c.execute('DELETE FROM objects WHERE sha=?', (r['sha'],))
        self._purge_files([(r['sha'], r['vault_path'], r['thumb_path']) for r in orphan])
        self.manifest()
        return {'objects': len(orphan)}

    def health(self):
        with self.db() as c:
            rows = [dict(r) for r in c.execute('SELECT f.id,f.name,f.origin_path,o.vault_path,o.integrity,o.size FROM files f JOIN objects o ON o.sha=f.sha')]
            failed = c.execute("SELECT count(*) FROM jobs WHERE state IN ('failed','partial')").fetchone()[0]
            backup = c.execute("SELECT result,updated_at FROM jobs WHERE kind='backup' AND state='done' ORDER BY updated_at DESC LIMIT 1").fetchone()
            automatic = c.execute("SELECT value FROM settings WHERE key='auto_backup'").fetchone()
            verification = c.execute("SELECT value FROM settings WHERE key='last_restore_verification'").fetchone()
            protected = c.execute('SELECT count(*),coalesce(sum(size),0) FROM objects WHERE vault_path IS NOT NULL').fetchone()
        offline = []; unique = []; changed = []; missing = []
        for f in rows:
            original = Path(f['origin_path']).is_file()
            copy = bool(f['vault_path'] and (self.root / f['vault_path']).is_file())
            entry = {'id': f['id'], 'name': f['name']}
            if not copy and not original: offline.append(entry)
            if copy and not original and not f['origin_path'].startswith('pack://'): unique.append(entry)
            if f['integrity'] == 'changed': changed.append(entry)
            if f['vault_path'] and not copy: missing.append(entry)
        external = [f for f in rows if not f['vault_path']]
        return {'offline': offline, 'unique': unique, 'changed': changed, 'missing': missing,
                'failed_jobs': failed, 'trash': len(self.trash_list()), 'last_backup': backup['updated_at'] if backup else None,
                'backup_coverage': {'managed_objects': protected[0], 'managed_bytes': protected[1],
                                    'external_files': len(external), 'external_bytes': sum(f['size'] for f in external)},
                'last_restore_verification': json.loads(verification[0]) if verification else None,
                'auto_backup': automatic is None or automatic[0] == '1', 'disk_free': shutil.disk_usage(self.root).free}

    def backup_list(self):
        return [{'name': p.name, 'path': str(p), 'bytes': p.stat().st_size} for p in sorted((self.root / 'backups').glob('*.shiyuebackup'), reverse=True)]

    def schedule_backup(self):
        with self.db() as c:
            settings = dict(c.execute("SELECT key,value FROM settings WHERE key IN ('auto_backup','backup_attempt')"))
            if settings.get('auto_backup', '1') != '1' or time.time() - float(settings.get('backup_attempt', '0')) < 86400: return
            if not c.execute('SELECT 1 FROM files LIMIT 1').fetchone(): return
            if c.execute("SELECT 1 FROM jobs WHERE kind='backup' AND state IN ('queued','running')").fetchone(): return
            c.execute("INSERT OR REPLACE INTO settings VALUES('backup_attempt',?)", (str(time.time()),))
        self.queue('backup', {'automatic': True})

    def job_backup(self, jid, payload):
        stage = Path(tempfile.mkdtemp(dir=self.root / 'tmp', prefix='backup-'))
        output = self.root / 'backups' / (datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6] + '.shiyuebackup')
        temp = output.with_suffix('.partial')
        try:
            # Registration/deletion holds the same lock; the snapshot's objects stay alive until copied.
            with self.lock:
                with closing(sqlite3.connect(self.dbpath)) as src, closing(sqlite3.connect(stage / 'library.sqlite3')) as dst: src.backup(dst)
                with closing(sqlite3.connect(stage / 'library.sqlite3')) as c:
                    # Compact the consistent snapshot. This never vacuums the live library.
                    c.execute('VACUUM')
                    if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall():
                        raise ValueError('备份数据库不完整')
                    objects = c.execute('SELECT sha,vault_path FROM objects WHERE vault_path IS NOT NULL').fetchall()
                    external_objects = [{'sha256': row[0], 'indexed_bytes': row[1]} for row in c.execute('SELECT sha,size FROM objects WHERE vault_path IS NULL ORDER BY sha')]
                    c.row_factory=sqlite3.Row
                    external_files = [dict(row) for row in c.execute('''SELECT f.id,f.sha AS sha256,f.name,f.origin_path,
                        f.source_id,s.name AS source_name,o.size AS indexed_bytes FROM files f
                        JOIN objects o ON o.sha=f.sha JOIN sources s ON s.id=f.source_id
                        WHERE o.vault_path IS NULL ORDER BY f.id''')]
                    verification = c.execute("SELECT value FROM settings WHERE key='last_restore_verification'").fetchone()
                external=len(external_objects)
                for record in external_files:
                    record['available_at_backup']=Path(record['origin_path']).is_file()
                total = sum((self.root / p).stat().st_size for s, p in objects if (self.root / p).is_file())
                if shutil.disk_usage(self.root).free < total + 32 * 1024 * 1024: raise ValueError('空间不足，未创建备份')
                checks = {}; self.job_update(jid, total=len(objects))
                with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
                    for index, (sha, rel) in enumerate(objects):
                        self.checkpoint()
                        if rel != f'objects/{sha[:2]}/{sha}': raise ValueError('对象路径异常')
                        source = self.root / rel
                        if source.is_symlink(): raise ValueError('对象不能是符号链接')
                        h = hashlib.sha256()
                        entry=zipfile.ZipInfo(rel)
                        entry.compress_type=zipfile.ZIP_STORED
                        with source.open('rb') as src, z.open(entry, 'w', force_zip64=True) as dst:
                            for b in iter(lambda: src.read(1024 * 1024), b''): dst.write(b); h.update(b)
                        if h.hexdigest() != sha: raise ValueError('托管内容损坏，备份已停止')
                        checks[rel] = sha; self.job_update(jid, done=index + 1)
                    db = stage / 'library.sqlite3'; z.write(db, 'library.sqlite3'); checks['library.sqlite3'] = sha256(db)
                    metadata=json.dumps({'format': 'shiyue-backup', 'version': 1, 'created_at': stamp(),
                        'external_objects': external, 'external_object_manifest': external_objects,
                        'external_files': external_files,
                        'last_restore_verification': json.loads(verification[0]) if verification else None,
                        'database_compacted': True, 'checksums': checks},ensure_ascii=False).encode()
                    if len(metadata)>64_000_000:raise ValueError('备份清单过大，未创建备份')
                    z.writestr('backup.json',metadata)
                with temp.open('rb') as f: os.fsync(f.fileno())
                os.replace(temp, output)
            return {'path': str(output), 'bytes': output.stat().st_size, 'external_objects': external,
                    'external_files': len(external_files), 'external_bytes': sum(f['indexed_bytes'] for f in external_files)}
        finally:
            temp.unlink(missing_ok=True); shutil.rmtree(stage, ignore_errors=True)

    def restore_backup(self, path):
        archive = Path(path).expanduser().resolve()
        if not archive.exists(): raise ValueError('备份不存在')
        from backup_store import RepositoryArchive
        reader = RepositoryArchive if archive.is_dir() else zipfile.ZipFile
        destination = self.root.parent / ('拾页恢复-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6])
        stage = Path(tempfile.mkdtemp(dir=self.root.parent, prefix='.shiyue-restore-'))
        try:
            with reader(archive) as z:
                names = z.namelist()
                if len(names) != len(set(names)) or len(names) > 1000003: raise ValueError('备份条目异常')
                if 'backup.json' not in names or z.getinfo('backup.json').file_size > 64_000_000: raise ValueError('备份清单无效')
                meta = json.loads(z.read('backup.json'))
                if not isinstance(meta,dict) or meta.get('format') != 'shiyue-backup' or meta.get('version') != 1: raise ValueError('备份格式不受支持')
                checks = meta.get('checksums', {})
                if not isinstance(checks, dict) or set(checks) != set(names) - {'backup.json'} or 'library.sqlite3' not in checks: raise ValueError('备份清单不完整')
                if any(not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value) for value in checks.values()): raise ValueError('备份校验清单无效')
                required = sum(z.getinfo(n).file_size for n in checks)
                if shutil.disk_usage(stage).free < required + 32 * 1024 * 1024: raise ValueError('恢复空间不足')
                for name, expected in checks.items():
                    if name != 'library.sqlite3' and not re.fullmatch(r'objects/[0-9a-f]{2}/[0-9a-f]{64}', name): raise ValueError('备份包含非法路径')
                    target = stage / name; target.parent.mkdir(parents=True, exist_ok=True); h = hashlib.sha256()
                    with z.open(name) as src, target.open('wb') as dst:
                        for b in iter(lambda: src.read(1024 * 1024), b''): dst.write(b); h.update(b)
                    if h.hexdigest() != expected: raise ValueError('备份校验失败，原资料库未改变')
                with closing(sqlite3.connect((stage / 'library.sqlite3').as_uri() + '?mode=ro', uri=True)) as c:
                    if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('备份数据库不完整')
                    for sha, rel in c.execute('SELECT sha,vault_path FROM objects WHERE vault_path IS NOT NULL'):
                        if rel != f'objects/{sha[:2]}/{sha}' or checks.get(rel) != sha: raise ValueError('备份对象与索引不一致')
                # Restore jobs must not repeat old exports/imports against the user's disks.
                with closing(sqlite3.connect(stage / 'library.sqlite3')) as c, c:
                    c.execute('UPDATE media SET version=0')
                    c.execute("DELETE FROM settings WHERE key='backup_attempt'")
                    if c.execute("SELECT 1 FROM sqlite_master WHERE name='source_watch'").fetchone(): c.execute('UPDATE source_watch SET enabled=0')
                    c.execute("UPDATE jobs SET state='cancelled',error='从备份恢复，旧任务不自动重跑' WHERE state IN ('running','queued')")
                restored=type(self)(stage,start_worker=False)
                try:
                    restored.manifest()
                    with restored.db() as c: shas=[r[0] for r in c.execute('SELECT sha FROM objects')]
                    for sha in shas: restored.sidecar(sha)
                finally: restored.close()
                os.replace(stage, destination)
            verification={'verified_at': stamp(), 'backup_path': str(archive), 'restored_path': str(destination),
                          'verified_objects': len(checks)-1, 'database_sha256': checks['library.sqlite3']}
            recorded=True
            try:
                with self.db() as c:
                    c.execute("INSERT OR REPLACE INTO settings VALUES('last_restore_verification',?)", (json.dumps(verification,ensure_ascii=False),))
                with closing(sqlite3.connect(destination/'library.sqlite3')) as c, c:
                    c.execute("INSERT OR REPLACE INTO settings VALUES('last_restore_verification',?)", (json.dumps(verification,ensure_ascii=False),))
            except sqlite3.Error:
                # The restored library is already verified and published. A failed
                # history write must not turn that successful restore into an error.
                recorded=False
            return {'path': str(destination), 'external_objects': meta.get('external_objects', 0),
                    'verified_at': verification['verified_at'], 'verification_recorded': recorded}
        except (zipfile.BadZipFile,sqlite3.DatabaseError,KeyError,json.JSONDecodeError,TypeError) as error:
            raise ValueError('备份格式或数据库不完整，原资料库未改变') from error
        finally:
            if stage.exists(): shutil.rmtree(stage, ignore_errors=True)

    def relocate_source(self, sid, new_root, apply=False):
        root = Path(new_root).expanduser()
        if root.is_symlink(): raise ValueError('请选择实际文件或文件夹')
        root = root.resolve()
        with self.db() as c:
            s = c.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
            if not s or s['kind'] == 'pack': raise ValueError('该来源不能重新定位')
            files = [dict(r) for r in c.execute('SELECT * FROM files WHERE source_id=?', (sid,))]
        if not root.exists() or root == self.root or self.root in root.parents: raise ValueError('新来源路径无效')
        if (s['kind'] == 'file') != root.is_file(): raise ValueError('请选择相同类型的来源')
        matches = []; changed = []; missing = []
        for f in files:
            try: candidate = root if s['kind'] == 'file' else root / Path(f['origin_path']).relative_to(s['root'])
            except ValueError: missing.append(f['name']); continue
            if candidate.is_symlink() or (s['kind'] != 'file' and root not in candidate.resolve().parents): missing.append(f['name']); continue
            if not candidate.is_file(): missing.append(f['name']); continue
            if sha256(candidate) != f['sha']: changed.append(f['name']); continue
            matches.append((str(candidate), f['id']))
        if apply:
            if not matches: raise ValueError('没有哈希一致的文件，未更改来源')
            with self.lock, self.db() as c:
                live = c.execute('SELECT root FROM sources WHERE id=?', (sid,)).fetchone()
                if not live or live[0] != s['root']: raise ValueError('来源已变化，请重新预览')
                c.executemany('UPDATE files SET origin_path=? WHERE id=?', matches)
                c.execute('UPDATE sources SET root=? WHERE id=?', (str(root), sid))
                c.execute('DELETE FROM scan_state WHERE file_id IN (SELECT id FROM files WHERE source_id=?)', (sid,))
            self.manifest()
        return {'matched': len(matches), 'changed': changed, 'missing': missing, 'applied': bool(apply)}

    def save_search(self, name, filters):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40: raise ValueError('搜索名称为 1–40 个字')
        allowed = {'q','kind','source','starred','sort','camera','month','collection','tag','after','before','include_children'}
        if not isinstance(filters, dict) or set(filters) - allowed or any(not isinstance(v, str) or len(v) > 300 for v in filters.values()): raise ValueError('搜索条件无效')
        with self.db() as c:
            c.execute('INSERT INTO saved_searches VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET filters=excluded.filters', (uuid.uuid4().hex, name.strip(), json.dumps(filters), stamp()))
        return self.saved_searches()

    def saved_searches(self):
        with self.db() as c: return [dict(r, filters=json.loads(r['filters'])) for r in c.execute('SELECT * FROM saved_searches ORDER BY created_at DESC')]

    def reading_position(self, fid, page=None):
        self.file_row(fid)
        if page is not None:
            if not isinstance(page, int) or not 1 <= page <= 100000: raise ValueError('页码无效')
            with self.db() as c: c.execute('INSERT OR REPLACE INTO reading VALUES(?,?,?)', (fid, page, stamp()))
        with self.db() as c: row = c.execute('SELECT page FROM reading WHERE file_id=?', (fid,)).fetchone()
        return {'page': row[0] if row else 1}

    def document_probe(self, fid, mode='info', page=1):
        f = self.file_row(fid); source = self._resolve(f)
        if not source: raise ValueError('原件离线')
        helper = Path(__file__).parent / 'bin/DocumentProbe'
        if not helper.exists(): raise ValueError('文档预览组件不可用，请重新构建应用')
        output = self.root / 'tmp/reader' / f['sha'] / f'{page}.jpg'; output.parent.mkdir(parents=True, exist_ok=True)
        if mode == 'page' and output.is_file(): return output
        result = subprocess.run([str(helper), json.dumps({'path': str(source), 'mode': mode, 'page': int(page), 'output': str(output)})], capture_output=True, text=True, timeout=120)
        if result.returncode: raise ValueError(result.stderr.strip()[:300] or '文档处理失败')
        data = json.loads(result.stdout); data['sha']=f['sha']
        return output if mode == 'page' else data

    def job_ocr(self, jid, payload):
        fid = payload.get('id'); f = self.file_row(fid)
        if f['kind'] != 'image' and not f['name'].lower().endswith('.pdf'): raise ValueError('OCR 支持图片和 PDF')
        if f['name'].lower().endswith('.svg'): raise ValueError('SVG 文字已直接索引，无需 OCR')
        with self.db() as c: cached=c.execute('SELECT chunks FROM ocr WHERE sha=?',(f['sha'],)).fetchone()
        if cached and not payload.get('force'):
            chunks=json.loads(cached[0]);return {'pages':max((ch['page'] for ch in chunks),default=1),'lines':len(chunks),'cached':True,'errors':[]}
        source=self._resolve(f)
        if not source or sha256(source)!=f['sha']: raise ValueError('原件离线或内容已变化，请重新扫描来源')
        info = self.document_probe(fid); pages = info.get('pages', 1); chunks = []; characters = 0
        if pages > 1000: raise ValueError('单次 OCR 最多 1000 页，请拆分后处理')
        self.job_update(jid, total=pages)
        for page in range(1, pages + 1):
            self.checkpoint(); data = self.document_probe(fid, 'ocr', page)
            for line in data.get('lines', []):
                text = line['text'].strip()
                if characters + len(text) > 2_000_000: raise ValueError('OCR 超过 200 万字上限，原索引未改变')
                characters += len(text)
                if text: chunks.append({'text': text, 'label': f'OCR · 第 {page} 页', 'page': page, 'offset': 0, 'ocr': True, 'bounds': line.get('bounds')})
            self.job_update(jid, done=page)
        if sha256(source)!=f['sha']: raise ValueError('识别期间文件发生变化，原索引未改变')
        with self.lock, self.db() as c:
            row = c.execute('SELECT chunks_json FROM objects WHERE sha=?', (f['sha'],)).fetchone()
            if not row: raise ValueError('资料已被移除')
            base = [ch for ch in json.loads(row[0]) if not ch.get('ocr')]
            c.execute('UPDATE objects SET chunks_json=? WHERE sha=?', (json.dumps(base + chunks, ensure_ascii=False), f['sha']))
            c.execute('INSERT OR REPLACE INTO ocr VALUES(?,?,?)', (f['sha'], json.dumps(chunks, ensure_ascii=False), stamp()))
            for row in c.execute('SELECT id FROM files WHERE sha=?', (f['sha'],)).fetchall(): self._index(c, row[0])
        self.sidecar(f['sha'])
        return {'pages': pages, 'lines': len(chunks), 'errors': []}
