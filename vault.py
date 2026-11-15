"""Local-only document vault. Originals are immutable; SQLite is authoritative."""
from __future__ import annotations
import csv, hashlib, io, json, mimetypes, os, re, shutil, sqlite3, tempfile, threading, time, uuid, zipfile
from contextlib import contextmanager, closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from safe_content import parse_html, decode_text, sanitize_svg
from media import build_media, MEDIA_VERSION, MEDIA_COLUMNS
from vault_features import Features
from knowledge_index import KnowledgeFeatures, initialize as init_knowledge, record_extraction
from source_watch import WatchFeatures
from backup_store import RepositoryFeatures
from knowledge_filters import CAPABILITIES
from organization import OrganizationFeatures
from collection_tree import CollectionFeatures, collection_rows, descendant_query

VERSION = '1.10.0'
EXTRACT_VERSION = 2
from document_extract import extract, TEXT_EXT, IMAGE_EXT, VIDEO_EXT, MAX_TEXT

class JobCancelled(Exception):
    """Raised inside a job whose row disappeared, i.e. the user deleted the task."""

def now(): return datetime.now(timezone.utc).isoformat()
def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
def atomic_write(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.writing-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def tokens(text):
    # CJK characters remain positions, allowing Chinese phrase matching without a model.
    return ' '.join(re.findall(r'[\u3400-\u9fff]|[^\W_]+', re.sub(r'([\u3400-\u9fff])', r' \1 ', text.lower()), re.UNICODE))
def kind_for(path):
    ext = Path(path).suffix.lower()
    return 'image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else 'document'

class Vault(OrganizationFeatures, CollectionFeatures, KnowledgeFeatures, WatchFeatures, RepositoryFeatures, Features):
    def __init__(self, root, start_worker=True):
        self.root=Path(root).expanduser().resolve(); self.root.mkdir(parents=True,exist_ok=True)
        for d in ('objects','meta','exports','tmp','thumbs','backups'): (self.root/d).mkdir(exist_ok=True)
        self.dbpath=self.root/'library.sqlite3'; self.stop=threading.Event(); self.wake=threading.Event(); self.lock=threading.RLock()
        # The id of the job the worker is running right now; checkpoint() uses it to
        # notice that the user deleted the task. Only one job runs at a time.
        self._current=None; self._purge=None
        # Hashing, extraction and the media helper are I/O bound; a 1 TB source spends
        # most of its wall clock waiting, so a few concurrent ingest lanes pay off.
        try: self.parallel=max(1,min(int(os.environ.get('SHIYE_PARALLEL','4')),8))
        except ValueError: self.parallel=4
        if self.dbpath.exists():
            with closing(sqlite3.connect(self.dbpath)) as source:
                columns={r[1] for r in source.execute('PRAGMA table_info(objects)')}
                if columns and not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trash'").fetchone():
                    backup12=self.root/'backups'/'pre-1.2.sqlite3'
                    if not backup12.exists():
                        with closing(sqlite3.connect(backup12)) as target: source.backup(target)
                if columns and 'extract_version' not in columns:
                    backup=self.root/'backups'/'pre-0.2-metadata.sqlite3'
                    if not backup.exists():
                        with closing(sqlite3.connect(backup)) as target: source.backup(target)
        with self.db() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,name TEXT,root TEXT,mode TEXT,kind TEXT,created_at TEXT,last_scan TEXT);
            CREATE TABLE IF NOT EXISTS objects(sha TEXT PRIMARY KEY,size INTEGER,mime TEXT,vault_path TEXT,created_at TEXT,chunks_json TEXT,warning TEXT,verified_at TEXT,integrity TEXT DEFAULT 'unchecked');
            CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,sha TEXT REFERENCES objects(sha),source_id TEXT REFERENCES sources(id),name TEXT,origin_path TEXT,kind TEXT,tags TEXT DEFAULT '[]',starred INTEGER DEFAULT 0,added_at TEXT,updated_at TEXT,UNIQUE(source_id,origin_path));
            CREATE TABLE IF NOT EXISTS revisions(file_id TEXT,sha TEXT,changed_at TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(file_id UNINDEXED,name,tags,body,tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,state TEXT,total INTEGER DEFAULT 0,done INTEGER DEFAULT 0,error TEXT,result TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            -- A collection is the user's own shelf: it references indexed files and
            -- never touches their originals. Many-to-many on purpose, so "AI资料"
            -- and "待读" can both point at the same document.
            CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS collection_files(collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,added_at TEXT,PRIMARY KEY(collection_id,file_id));
            CREATE TABLE IF NOT EXISTS media(sha TEXT PRIMARY KEY REFERENCES objects(sha),width INTEGER,height INTEGER,duration REAL,captured_at TEXT,captured_ts REAL,captured_source TEXT,camera TEXT,make TEXT,gps_lat REAL,gps_lon REAL,codec TEXT,thumb_path TEXT,media_error TEXT DEFAULT '',version INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS media_date ON media(captured_at);
            CREATE INDEX IF NOT EXISTS media_camera ON media(camera);
            CREATE INDEX IF NOT EXISTS files_sha ON files(sha);
            CREATE INDEX IF NOT EXISTS files_source ON files(source_id);
            CREATE INDEX IF NOT EXISTS revisions_file ON revisions(file_id);
            CREATE INDEX IF NOT EXISTS revisions_sha ON revisions(sha);
            CREATE INDEX IF NOT EXISTS collection_files_file ON collection_files(file_id);
            ''')
            if "extract_version" not in {r[1] for r in c.execute("PRAGMA table_info(objects)")}:
                c.execute("ALTER TABLE objects ADD COLUMN extract_version INTEGER DEFAULT 0")
            if "captured_ts" not in {r[1] for r in c.execute("PRAGMA table_info(media)")}:
                c.execute("ALTER TABLE media ADD COLUMN captured_ts REAL")
            # The index must follow the column: an older library gains captured_ts above,
            # so creating it inside the schema script would fail on first open.
            c.execute("CREATE INDEX IF NOT EXISTS media_ts ON media(captured_ts)")
            c.execute("UPDATE jobs SET state='queued' WHERE state='running'")
            stale=c.execute("SELECT 1 FROM files f JOIN objects o ON f.sha=o.sha LEFT JOIN media m ON m.sha=f.sha WHERE ((lower(f.name) LIKE '%.html' OR lower(f.name) LIKE '%.htm' OR lower(f.name) LIKE '%.svg') AND o.extract_version<?) OR (f.kind IN ('image','video') AND coalesce(m.version,0)<?) LIMIT 1",(EXTRACT_VERSION,MEDIA_VERSION)).fetchone()
            if stale and not c.execute("SELECT 1 FROM jobs WHERE kind='refresh' AND state IN ('queued','running')").fetchone():
                c.execute("INSERT INTO jobs(id,kind,payload,state,created_at,updated_at) VALUES(?,'refresh','{}','queued',?,?)",(uuid.uuid4().hex,now(),now()))
        self.init_collections()
        self.init_features()
        init_knowledge(self)
        self.init_organization()
        self.init_watch()
        self.worker=None
        if start_worker:
            self.worker=threading.Thread(target=self._worker,daemon=True); self.worker.start()
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.dbpath,timeout=30); c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA synchronous=FULL')
        try:
            yield c; c.commit()
        except Exception: c.rollback(); raise
        finally: c.close()
    def close(self):
        self.stop.set(); self.wake.set()
        self.stop_watch()
        if self.worker: self.worker.join(timeout=5)
        if self._purge: self._purge.join(timeout=30)
    def paused(self):
        with self.db() as c: row=c.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        return bool(row and row[0]=='1')
    def set_pause(self, value):
        with self.db() as c: c.execute("INSERT OR REPLACE INTO settings VALUES('paused',?)",('1' if value else '0',))
        self.wake.set()
    def checkpoint(self):
        # One query answers both "paused?" and "does my task still exist?". A deleted
        # row means cancelled, so deleting a running task needs no extra state to
        # track and cannot leak between jobs.
        while True:
            with self.db() as c:
                row=c.execute("SELECT (SELECT value FROM settings WHERE key='paused'),(SELECT state FROM jobs WHERE id=?)",(self._current or '',)).fetchone()
            if self.stop.is_set(): raise InterruptedError('任务将在下次启动后继续')
            if self._current and row[1]!='running': raise JobCancelled('任务已取消')
            if row[0]!='1': return
            self.wake.wait(.4); self.wake.clear()
    def queue(self, kind, payload, request_id=None):
        with self.db() as c: result=self._queue_in_transaction(c,kind,payload,request_id)
        self.wake.set(); return result
    def _queue_in_transaction(self, c, kind, payload, request_id=None):
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=160):
            raise ValueError('请求标识无效')
        job=uuid.uuid5(uuid.NAMESPACE_URL,'shiyue-import:'+request_id).hex if request_id else uuid.uuid4().hex
        body=json.dumps(payload,sort_keys=True)
        prior=c.execute('SELECT kind,payload FROM jobs WHERE id=?',(job,)).fetchone()
        if prior and (prior['kind']!=kind or json.loads(prior['payload'])!=payload):raise ValueError('同一请求标识不能用于不同导入')
        c.execute('INSERT OR IGNORE INTO jobs(id,kind,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?)',(job,kind,body,'queued',now(),now()))
        return {'job_id':job}

    def job_receipt(self, job):
        with self.db() as c: row=c.execute('SELECT id,kind,state,total,done,error,result FROM jobs WHERE id=?',(job,)).fetchone()
        if not row:raise ValueError('任务不存在')
        result=dict(row);result['result']=json.loads(result['result']) if result['result'] else None
        return result

    def job_update(self, job, **values):
        values['updated_at']=now()
        with self.db() as c: c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[job])
    def retry(self, job):
        with self.db() as c: c.execute("UPDATE jobs SET state='queued',error=NULL WHERE id=? AND state IN ('failed','partial')",(job,))
        self.wake.set()
    def delete_job(self, job):
        """Forget a task. A running task stops at its next checkpoint.

        Files already imported stay in the library: the index is the user's data,
        the task list is only a record of the work. Disconnecting the source is the
        way to remove those files.
        """
        with self.db() as c:
            row=c.execute('SELECT state,kind FROM jobs WHERE id=?',(job,)).fetchone()
            if not row: raise ValueError('任务不存在，可能已被删除')
            c.execute('DELETE FROM jobs WHERE id=?',(job,))
        self.wake.set(); return {'state':row['state'],'kind':row['kind']}
    def add_source(self, path, mode='managed', kind='folder', name=None):
        path=Path(path).expanduser().resolve()
        if mode not in ('managed','in-place','read-only'): raise ValueError('无效存储模式')
        if kind!='pack' and (path==self.root or self.root in path.parents): raise ValueError('不能导入资料库自身目录')
        with self.db() as c:
            row=c.execute('SELECT id FROM sources WHERE root=? AND mode=? AND kind=?',(str(path),mode,kind)).fetchone()
            if row: return row[0]
            sid=uuid.uuid4().hex
            c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,NULL)',(sid,name or path.name,str(path),mode,kind,now()))
        return sid
    def submit_import(self, paths, mode, request_id=None, file_guards=None, classification_plan_id=None):
        if mode not in ('managed','in-place'): raise ValueError('请选择托管复制或就地索引')
        if not isinstance(paths,(list,tuple)) or not 1<=len(paths)<=1000: raise ValueError('请选择 1–1000 个文件或文件夹')
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=160):raise ValueError('请求标识无效')
        if file_guards is not None and not isinstance(file_guards,dict):raise ValueError('文件校验记录无效')
        # Validate the whole batch before creating sources. Source and queue writes
        # then share one transaction, including rejection of reused request IDs.
        prepared=[]
        for raw in paths:
            if not isinstance(raw,(str,Path)) or not str(raw) or '\0' in str(raw):raise ValueError('文件路径无效')
            p=Path(raw).expanduser()
            if p.is_symlink(): raise ValueError('首版不跟随符号链接，请选择实际文件')
            p=p.resolve()
            if not p.exists(): raise ValueError(f'路径不存在：{p}')
            if p==self.root or self.root in p.parents:raise ValueError('不能导入资料库自身目录')
            if not p.is_dir() and not p.is_file():raise ValueError('只能导入普通文件或文件夹')
            prepared.append((p,'folder' if p.is_dir() else 'file'))
        if file_guards is not None:
            from disk_discovery import validate_candidate
            if set(file_guards)!={str(p) for p,_ in prepared}:raise ValueError('文件校验范围与导入列表不一致')
            for p,_ in prepared:validate_candidate(p,file_guards[str(p)])
        items=[]
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if classification_plan_id:
                _, prior_job = self.classification_plan(c,classification_plan_id,[str(p) for p,_ in prepared],file_guards,mode)
                if prior_job:return {'job_id':prior_job,'reused':True,'classification_batch_id':classification_plan_id}
            for p,kind in prepared:
                row=c.execute('SELECT id FROM sources WHERE root=? AND mode=? AND kind=?',(str(p),mode,kind)).fetchone()
                if row:sid=row['id']
                else:
                    sid=uuid.uuid4().hex
                    c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,NULL)',(sid,p.name,str(p),mode,kind,now()))
                    if file_guards is not None:
                        # Selecting a particular file version does not authorize a
                        # later unguarded rescan after that selection was rejected.
                        c.execute('INSERT INTO source_watch(source_id,enabled) VALUES(?,0)',(sid,))
                items.append({'path':str(p),'source_id':sid})
            result=self._queue_in_transaction(c,'import',{'items':items,'mode':mode,**({'receipt':True} if request_id else {}),**({'file_guards':file_guards} if file_guards is not None else {}),**({'classification_plan_id':classification_plan_id} if classification_plan_id else {})},request_id)
            if classification_plan_id:
                c.execute('UPDATE classification_plans SET job_id=?,mode=? WHERE id=?',(result['job_id'],mode,classification_plan_id))
                result['classification_batch_id']=classification_plan_id
        self.wake.set();return result
    def rescan(self, sid, incremental=False):
        with self.db() as c: s=c.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
        if not s or s['kind']=='pack': raise ValueError('该来源不能扫描')
        if not Path(s['root']).exists(): raise ValueError('来源离线，请连接硬盘后重试')
        return self.queue('import',{'items':[{'path':s['root'],'source_id':sid}],'mode':s['mode'],'incremental':bool(incremental)})
    def source_impact(self, sid):
        """What disconnecting this source would remove, shown before the user commits."""
        with self.db() as c:
            s=c.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
            if not s: raise ValueError('来源不存在，可能已被断开')
            files,objects,managed=c.execute('SELECT count(*),count(DISTINCT f.sha),coalesce(sum(CASE WHEN o.vault_path IS NOT NULL THEN o.size ELSE 0 END),0) FROM files f JOIN objects o ON o.sha=f.sha WHERE f.source_id=?',(sid,)).fetchone()
            # Counting distinct hashes first keeps this at ~30 ms for a 21k-file
            # Desktop import; the correlated form took 900 ms there.
            shared=c.execute('SELECT count(*) FROM (SELECT DISTINCT sha FROM files WHERE source_id=?) x WHERE EXISTS(SELECT 1 FROM files g WHERE g.sha=x.sha AND g.source_id<>?)',(sid,sid)).fetchone()[0]
        return {'source':dict(s),'files':files,'objects':objects,'shared':shared,'removed':objects-shared,'bytes':managed}
    def file_impact(self, ids):
        """What removing these files would cost, including the case that matters most:
        a managed copy whose original is gone is the last surviving copy."""
        ids=self._checked_ids(ids)
        with self.db() as c:
            rows=[dict(r) for r in c.execute('SELECT f.id,f.name,f.sha,f.origin_path,o.vault_path,o.size,s.mode FROM files f JOIN objects o ON o.sha=f.sha JOIN sources s ON s.id=f.source_id WHERE f.id IN ('+','.join('?'*len(ids))+')',ids)]
            if not rows: raise ValueError('资料不存在，可能已被移除')
            shas={r['sha'] for r in rows}
            shared=c.execute('SELECT count(*) FROM (SELECT DISTINCT sha FROM files WHERE id NOT IN ('+','.join('?'*len(ids))+')) x WHERE x.sha IN ('+','.join('?'*len(shas))+')',ids+list(shas)).fetchone()[0]
        bytes_freed=0; offline=0; unique=0
        for r in rows:
            copy=(self.root/r['vault_path']) if r['vault_path'] else None
            has_copy=bool(copy and copy.is_file())
            if has_copy: bytes_freed+=r['size'] or 0
            # A pack's origin is a virtual pack:// address, not a path we can stat.
            origin_missing=r['mode']!='read-only' and not Path(r['origin_path']).is_file()
            if origin_missing and not has_copy: offline+=1
            elif origin_missing and has_copy: unique+=1
        return {'files':len(rows),'objects':len(shas),'bytes':bytes_freed,'shared':shared,'offline':offline,'unique':unique}
    def _checked_ids(self, ids):
        if not isinstance(ids,(list,tuple,set)): raise ValueError('请先选择要移除的资料')
        ids=list(dict.fromkeys(i for i in ids if isinstance(i,str) and i))
        if not ids: raise ValueError('请先选择要移除的资料')
        if len(ids)>1000: raise ValueError('一次最多移除 1000 份资料')
        return ids
    def permanently_remove_files(self, ids):
        """Forget individual files, whether or not their originals still exist.

        Removing an entry whose original was deleted elsewhere is the whole point:
        nothing on disk changes, the record and its extracted text simply stop
        existing.
        """
        return self._forget(self._checked_ids(ids))
    def permanently_remove_source(self, sid):
        """Disconnect a source and forget everything indexed from it.

        This never touches the user's original files: a managed copy inside the
        library is a convenience, not their only copy. Objects another source still
        references are kept, everything else is unlinked in the background so a
        250k-file photo folder does not block the request.
        """
        cancelled=0
        with self.db() as c:
            for row in c.execute("SELECT id,payload FROM jobs WHERE state IN ('queued','running')").fetchall():
                if sid in (row['payload'] or ''):
                    c.execute('DELETE FROM jobs WHERE id=?',(row['id'],)); cancelled+=1
        with self.db() as c:
            if not c.execute('SELECT 1 FROM sources WHERE id=?',(sid,)).fetchone(): raise ValueError('来源不存在，可能已被断开')
            ids=[r[0] for r in c.execute('SELECT id FROM files WHERE source_id=?',(sid,))]
        result=self._forget(ids,source_id=sid); result['cancelled_jobs']=cancelled
        return result
    def _forget(self, ids, source_id=None):
        """Drop file records, their index rows and any object left unreferenced.

        One transaction decides what becomes an orphan, so a file that shares its
        bytes with another file - or that another file's version history still
        points at - keeps its object.
        """
        with self.db() as c:
            c.execute('CREATE TEMP TABLE IF NOT EXISTS doomed(id TEXT PRIMARY KEY)')
            c.execute('DELETE FROM doomed')
            c.executemany('INSERT OR IGNORE INTO doomed VALUES(?)',[(i,) for i in ids])
            files=c.execute('SELECT count(*) FROM files WHERE id IN (SELECT id FROM doomed)').fetchone()[0]
            owners=[r[0] for r in c.execute('SELECT DISTINCT source_id FROM files WHERE id IN (SELECT id FROM doomed)')]
            c.execute('CREATE TEMP TABLE IF NOT EXISTS purge(sha TEXT PRIMARY KEY,vault_path TEXT,thumb_path TEXT)')
            c.execute('DELETE FROM purge')
            c.execute('''INSERT OR IGNORE INTO purge
                SELECT o.sha,o.vault_path,m.thumb_path FROM objects o
                JOIN (SELECT DISTINCT f.sha FROM files f JOIN doomed d ON d.id=f.id) x ON x.sha=o.sha
                LEFT JOIN media m ON m.sha=o.sha
                WHERE NOT EXISTS(SELECT 1 FROM files f WHERE f.sha=o.sha AND f.id NOT IN (SELECT id FROM doomed))
                  AND NOT EXISTS(SELECT 1 FROM revisions r WHERE r.sha=o.sha AND r.file_id NOT IN (SELECT id FROM doomed))
                  AND NOT EXISTS(SELECT 1 FROM trash_refs t WHERE t.sha=o.sha)''')
            purged,bytes_freed=c.execute('SELECT count(*),coalesce(sum(CASE WHEN o.vault_path IS NOT NULL THEN o.size ELSE 0 END),0) FROM purge p JOIN objects o ON o.sha=p.sha').fetchone()
            c.execute('DELETE FROM search WHERE file_id IN (SELECT id FROM doomed)')
            c.execute('DELETE FROM revisions WHERE file_id IN (SELECT id FROM doomed)')
            c.execute('DELETE FROM files WHERE id IN (SELECT id FROM doomed)')
            # media references objects, so it has to go first or the foreign key blocks.
            c.execute('DELETE FROM media WHERE sha IN (SELECT sha FROM purge)')
            c.execute('DELETE FROM objects WHERE sha IN (SELECT sha FROM purge)')
            if source_id: c.execute('DELETE FROM sources WHERE id=?',(source_id,))
            # A single-file source exists only for its file, so it leaves with it.
            for sid in owners:
                c.execute("DELETE FROM sources WHERE id=? AND kind='file' AND NOT EXISTS(SELECT 1 FROM files WHERE source_id=?)",(sid,sid))
            orphans=[tuple(r) for r in c.execute('SELECT sha,vault_path,thumb_path FROM purge')]
        self.manifest()
        if orphans:
            self._purge=threading.Thread(target=self._purge_files,args=(orphans,),daemon=True); self._purge.start()
        return {'files':files,'objects':purged,'bytes':bytes_freed}
    def _purge_files(self, orphans):
        for start in range(0,len(orphans),200):
            batch=orphans[start:start+200]
            with self.db() as c:
                live={r[0] for r in c.execute('SELECT sha FROM objects WHERE sha IN ('+','.join('?'*len(batch))+')',[r[0] for r in batch])}
            for sha,vault_path,thumb_path in batch:
                # Re-importing the same bytes between commit and unlink recreates the
                # row; content addressing makes that cheap to check before deleting.
                if sha in live: continue
                for rel in (vault_path,thumb_path,'meta/'+sha[:2]+'/'+sha+'.json'):
                    if not rel: continue
                    try: (self.root/rel).unlink()
                    except OSError: pass
                for folder in ('objects','thumbs','meta'):
                    try: (self.root/folder/sha[:2]).rmdir()
                    except OSError: pass
    def iter_files(self, p):
        p=Path(p)
        if p.is_file():
            if p.resolve()==self.root or self.root in p.resolve().parents: return
            yield p; return
        for base, dirs, files in os.walk(p,followlinks=False):
            dirs[:]=[d for d in dirs if not d.startswith('.') and not (Path(base)/d).is_symlink() and (Path(base)/d).resolve()!=self.root and not d.endswith('.app')]
            for n in files:
                f=Path(base)/n
                if not n.startswith('.') and not f.is_symlink() and f.is_file(): yield f
    def _index(self,c,fid):
        f=c.execute('SELECT f.*,o.chunks_json FROM files f JOIN objects o ON f.sha=o.sha WHERE f.id=?',(fid,)).fetchone()
        c.execute('DELETE FROM search WHERE file_id=?',(fid,))
        chunks=json.loads(f['chunks_json'])
        record_extraction(c,f['sha'],chunks)
        body='\n'.join(x['text'] for x in chunks)
        c.execute('INSERT INTO search VALUES(?,?,?,?)',(fid,tokens(f['name']),tokens(' '.join(json.loads(f['tags']))),tokens(body)))
    def ingest(self,path,sid,mode,tags=None,starred=None,name=None,origin=None):
        path=Path(path); before=path.stat(); sha=digest(path)
        if (before.st_size,before.st_mtime_ns)!=(path.stat().st_size,path.stat().st_mtime_ns): raise ValueError('文件在读取过程中发生变化，请重试')
        target=self.root/'objects'/sha[:2]/sha
        managed=mode in ('managed','read-only')
        if managed:
            target.parent.mkdir(exist_ok=True)
            if not target.exists():
                fd,tmp=tempfile.mkstemp(dir=target.parent,prefix='.copy-'); os.close(fd)
                try:
                    with path.open('rb') as src, open(tmp,'wb') as dst: shutil.copyfileobj(src,dst,1024*1024); dst.flush(); os.fsync(dst.fileno())
                    if digest(tmp)!=sha: raise ValueError('复制校验失败，原件未修改')
                    os.replace(tmp,target)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
            elif digest(target)!=sha: raise ValueError('资料库中已有对象校验失败，请先恢复备份')
        filename=name or path.name
        with self.db() as c: obj=c.execute('SELECT * FROM objects WHERE sha=?',(sha,)).fetchone()
        chunks,warning=extract(target if managed else path,filename,cancel=self.stop) if not obj or obj['extract_version']<EXTRACT_VERSION else (json.loads(obj['chunks_json']),obj['warning'])
        media_data=None
        if kind_for(filename) in ('image','video'):
            with self.db() as c: previous=c.execute('SELECT * FROM media WHERE sha=?',(sha,)).fetchone()
            if not previous or previous['version']<MEDIA_VERSION or (previous['thumb_path'] and not (self.root/previous['thumb_path']).is_file()):
                media_data=build_media(target if managed else path,filename,sha,self.root)
        if not managed:
            after=path.stat()
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError('文件在建立索引期间发生变化，请重新扫描')
        stamp=now(); origin=origin or str(path.resolve())
        with self.lock,self.db() as c:
            c.execute('INSERT OR IGNORE INTO objects(sha,size,mime,vault_path,created_at,chunks_json,warning,verified_at,integrity) VALUES(?,?,?,?,?,?,?,?,?)',(sha,before.st_size,mimetypes.guess_type(filename)[0] or 'application/octet-stream',str(target.relative_to(self.root)) if managed else None,stamp,json.dumps(chunks,ensure_ascii=False),warning,stamp,'ok'))
            c.execute('UPDATE objects SET chunks_json=?,warning=?,extract_version=? WHERE sha=?',(json.dumps(chunks,ensure_ascii=False),warning,EXTRACT_VERSION,sha))
            if media_data is not None: self._save_media(c,sha,media_data)
            if managed: c.execute("UPDATE objects SET vault_path=?,integrity='ok',verified_at=? WHERE sha=?",(str(target.relative_to(self.root)),stamp,sha))
            old=c.execute('SELECT * FROM files WHERE source_id=? AND origin_path=?',(sid,origin)).fetchone()
            fid=old['id'] if old else uuid.uuid4().hex
            if old:
                if old['sha']!=sha: c.execute('INSERT INTO revisions VALUES(?,?,?)',(fid,old['sha'],stamp))
                c.execute('UPDATE files SET sha=?,name=?,kind=?,updated_at=? WHERE id=?',(sha,filename,kind_for(filename),stamp,fid))
            else: c.execute('INSERT INTO files(id,sha,source_id,name,origin_path,kind,added_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(fid,sha,sid,filename,origin,kind_for(filename),stamp,stamp))
            if tags is not None: c.execute('UPDATE files SET tags=? WHERE id=?',(json.dumps(tags,ensure_ascii=False),fid))
            if starred is not None: c.execute('UPDATE files SET starred=? WHERE id=?',(int(bool(starred)),fid))
            for ref in c.execute('SELECT id FROM files WHERE sha=?',(sha,)).fetchall():self._index(c,ref[0])
        self.sidecar(sha)
        return fid
    def _save_media(self,c,sha,data):
        c.execute('INSERT OR REPLACE INTO media(sha,'+','.join(MEDIA_COLUMNS)+') VALUES('+','.join('?' for _ in range(len(MEDIA_COLUMNS)+1))+')',[sha]+[data.get(key) for key in MEDIA_COLUMNS])
    def _media_fields(self,f):
        f['has_thumbnail']=bool(f.get('thumb_path') and (self.root/f['thumb_path']).is_file())
        return f
    def thumbnail(self,fid):
        f=self.file_row(fid)
        if not f.get('thumb_path') or not (self.root/f['thumb_path']).is_file():raise ValueError(f.get('media_error') or '预览尚未生成，请更新预览与索引')
        return self.root/f['thumb_path']
    def sidecar(self,sha):
        with self.db() as c:
            o=dict(c.execute('SELECT * FROM objects WHERE sha=?',(sha,)).fetchone())
            media=c.execute('SELECT * FROM media WHERE sha=?',(sha,)).fetchone()
            o['media']=dict(media) if media else None
            o['files']=[dict(r) for r in c.execute('SELECT f.*,s.name AS source_name FROM files f JOIN sources s ON f.source_id=s.id WHERE sha=?',(sha,))]
            collection_ids={r[0] for r in c.execute('SELECT DISTINCT cf.collection_id FROM collection_files cf JOIN files f ON f.id=cf.file_id WHERE f.sha=?',(sha,))}
            o['collections']=[row['path_label'] for row in collection_rows(c) if row['id'] in collection_ids]
        o.pop('chunks_json',None)
        for f in o['files']: f['tags']=json.loads(f['tags'])
        atomic_write(self.root/'meta'/sha[:2]/(sha+'.json'),json.dumps(o,ensure_ascii=False,indent=2).encode())
    def manifest(self):
        out=io.StringIO(); w=csv.writer(out); w.writerow(['id','filename','sha256','bytes','source','origin_path','vault_path','tags'])
        with self.db() as c:
            for r in c.execute('SELECT f.*,o.size,o.vault_path,s.name AS source FROM files f JOIN objects o ON f.sha=o.sha JOIN sources s ON f.source_id=s.id ORDER BY f.added_at'): w.writerow([r[k] for k in ('id','name','sha','size','source','origin_path','vault_path','tags')])
        atomic_write(self.root/'manifest.csv',('\ufeff'+out.getvalue()).encode())
        # Collections live in their own CSV: the main manifest keeps its shape for
        # anything already reading it, and one file per shelf is easier to diff.
        out=io.StringIO(); w=csv.writer(out); w.writerow(['collection','file_id','filename','sha256'])
        with self.db() as c:
            labels={row['id']:row['path_label'] for row in collection_rows(c)}
            for r in c.execute('SELECT cf.collection_id,f.id,f.name AS filename,f.sha FROM collection_files cf JOIN files f ON f.id=cf.file_id ORDER BY cf.added_at,f.name'):
                w.writerow([labels[r['collection_id']],r['id'],r['filename'],r['sha']])
        atomic_write(self.root/'collections.csv',('\ufeff'+out.getvalue()).encode())
    def _resolve(self,f):
        if f['vault_path']:
            p=self.root/f['vault_path']
            return p if p.is_file() else None
        p=Path(f['origin_path'])
        if p.is_file() and f['mode']!='read-only': return p
        return None
    def open_target(self,f):
        """Where "open" and "reveal" should go: the user's own file first.

        _resolve() prefers the managed object because preview, export and verify
        all want immutable bytes. Handing that path to Finder or to an editor is
        wrong, though: the user knows their file as ~/WorkBuddy AI/... and would
        be shown data/objects/1c/1cf6... instead. The copy is only a fallback for
        when the original really is gone.
        """
        if f['mode']!='read-only':
            p=Path(f['origin_path'])
            if p.is_file(): return p,'origin'
        p=self._resolve(f)
        return (p,'copy') if p else (None,None)
    def file_row(self,fid):
        with self.db() as c: r=c.execute('SELECT f.*,o.size,o.mime,o.vault_path,o.warning,o.chunks_json,o.integrity,o.verified_at,m.width,m.height,m.duration,m.captured_at,m.captured_source,m.camera,m.make,m.gps_lat,m.gps_lon,m.codec,m.thumb_path,m.media_error,m.version,s.name AS source_name,s.mode,s.kind AS source_kind FROM files f JOIN objects o ON f.sha=o.sha JOIN sources s ON f.source_id=s.id LEFT JOIN media m ON m.sha=f.sha WHERE f.id=?',(fid,)).fetchone()
        if not r: raise ValueError('文件记录不存在')
        return self._media_fields(dict(r))
    def detail(self,fid):
        f=self.file_row(fid); f['tags']=json.loads(f['tags']); f['chunks']=json.loads(f.pop('chunks_json')); f['available']=self._resolve(f) is not None
        # A managed copy keeps the file openable after the original is deleted, so
        # "available" alone would hide the fact that this is the last copy left.
        f['origin_missing']=f['mode']!='read-only' and not Path(f['origin_path']).is_file()
        with self.db() as c: f['versions']=[dict(r) for r in c.execute('SELECT sha,changed_at FROM revisions WHERE file_id=? ORDER BY changed_at DESC',(fid,))]
        f['collections']=self.file_collections(fid)
        f['preference']=self.preference(fid)
        return f
    def edit(self,fid,tags=None,starred=None):
        f=self.file_row(fid)
        if f['mode']=='read-only': raise ValueError('外部包为只读来源')
        if tags is not None and (not isinstance(tags,list) or len(tags)>50 or any(not isinstance(t,str) or len(t)>80 for t in tags)): raise ValueError('标签格式无效')
        with self.lock,self.db() as c:
            if tags is not None: c.execute('UPDATE files SET tags=?,updated_at=? WHERE id=?',(json.dumps(list(dict.fromkeys(t.strip() for t in tags if t.strip())),ensure_ascii=False),now(),fid))
            if starred is not None: c.execute('UPDATE files SET starred=?,updated_at=? WHERE id=?',(int(bool(starred)),now(),fid))
            self._index(c,fid)
        self.sidecar(f['sha']); self.manifest()
        return self.detail(fid)
    def list_files(self,q='',kind='',source='',starred=False,offset=0,limit=60,sort='added',camera='',month='',collection='',tag='',after='',before='',include_children=True):
        begin=time.perf_counter(); args=[]; where=[]; join=''; order='f.added_at DESC'
        if q.strip():
            parts=[tokens(p) for p in q[:200].split() if tokens(p)]
            if parts:
                join=' JOIN search ON search.file_id=f.id'; where.append('search MATCH ?'); args.append(' AND '.join('"'+p.replace('"','""')+'"' for p in parts)); order='bm25(search,0,8,5,1),f.added_at DESC'
        if sort=='captured':order='m.captured_ts IS NULL, m.captured_ts DESC, f.id'
        elif sort=='name':order='f.name COLLATE NOCASE,f.id'
        if camera=='__unknown__':where.append("f.kind IN ('image','video') AND (m.camera IS NULL OR m.camera='')")
        elif camera:where.append('m.camera=?');args.append(camera)
        if month=='__unknown__':where.append("f.kind IN ('image','video') AND m.captured_at IS NULL")
        elif month:
            if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',month):raise ValueError('日期筛选格式无效')
            where.append('m.captured_at LIKE ?');args.append(month+'%')
        if kind in ('document','image','video'): where.append('f.kind=?'); args.append(kind)
        if source: where.append('f.source_id=?'); args.append(source)
        if collection:
            if include_children:
                where.append('f.id IN (SELECT file_id FROM collection_files WHERE collection_id IN ('+descendant_query()+'))')
            else:
                where.append('f.id IN (SELECT file_id FROM collection_files WHERE collection_id=?)')
            args.append(collection)
        if starred: where.append('f.starred=1')
        if tag:
            where.append('EXISTS(SELECT 1 FROM json_each(f.tags) WHERE value=?)'); args.append(tag)
        for value,op in ((after,'>='),(before,'<=')):
            if value:
                try: datetime.strptime(value,'%Y-%m-%d')
                except ValueError: raise ValueError('加入日期无效')
                where.append('substr(f.added_at,1,10)'+op+'?'); args.append(value)
        base=' FROM files f JOIN objects o ON f.sha=o.sha JOIN sources s ON f.source_id=s.id LEFT JOIN media m ON m.sha=f.sha'+join+(' WHERE '+' AND '.join(where) if where else '')
        with self.db() as c:
            count=c.execute('SELECT count(*)'+base,args).fetchone()[0]
            rows=[dict(r) for r in c.execute('SELECT f.*,o.size,o.warning,o.integrity,o.vault_path,o.chunks_json,m.width,m.height,m.duration,m.captured_at,m.captured_source,m.camera,m.make,m.gps_lat,m.gps_lon,m.codec,m.thumb_path,m.media_error,m.version,s.name AS source_name,s.mode'+base+' ORDER BY '+order+' LIMIT ? OFFSET ?',args+[min(max(limit,1),100),max(offset,0)])]
        for r in rows:
            self._media_fields(r)
            chunks=json.loads(r.pop('chunks_json')); r['tags']=json.loads(r['tags']); r['available']=self._resolve(r) is not None
            match=next((ch for ch in chunks if q.strip().lower() in ch['text'].lower()),chunks[0] if chunks else None)
            r['excerpt']=''; r['anchor']=None
            if match:
                text=match['text']; pos=max(0,text.lower().find(q.strip().lower())-55) if q else 0
                r['excerpt']=text[pos:pos+170]; r['anchor']={'label':match['label'],'page':match['page'],'offset':match['offset']}
        return {'files':rows,'total':count,'elapsed_ms':round((time.perf_counter()-begin)*1000,1)}
    def status(self):
        with self.db() as c:
            counts={r[0]:r[1] for r in c.execute('SELECT kind,count(*) FROM files GROUP BY kind')}
            stats=dict(c.execute('SELECT count(*) AS objects,coalesce(sum(size),0) AS bytes,coalesce(sum(CASE WHEN vault_path IS NOT NULL THEN size ELSE 0 END),0) AS managed_bytes FROM objects').fetchone())
            stats['files']=sum(counts.values()); stats['counts']=counts; stats['starred']=c.execute('SELECT count(*) FROM files WHERE starred=1').fetchone()[0]
            sources=[dict(r) for r in c.execute('SELECT s.*,count(f.id) AS count FROM sources s LEFT JOIN files f ON s.id=f.source_id GROUP BY s.id ORDER BY s.created_at')]
            jobs=[dict(r) for r in c.execute('SELECT id,kind,state,total,done,error,result,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT 30')]
            cameras=[r[0] for r in c.execute("SELECT DISTINCT m.camera FROM media m JOIN files f ON f.sha=m.sha WHERE m.camera IS NOT NULL AND m.camera!='' ORDER BY m.camera")]
            months=[r[0] for r in c.execute("SELECT DISTINCT substr(m.captured_at,1,7) FROM media m JOIN files f ON f.sha=m.sha WHERE m.captured_at IS NOT NULL ORDER BY 1 DESC")]
            stats['warnings']=c.execute("SELECT count(*) FROM files f JOIN objects o ON f.sha=o.sha WHERE o.warning!=''").fetchone()[0]
            collections=collection_rows(c)
        with self.db() as c: watches={r['source_id']:dict(r) for r in c.execute('SELECT * FROM source_watch')}
        for s in sources:
            s['online']=s['kind']=='pack' or Path(s['root']).exists()
            s['watch']=watches.get(s['id'],{'enabled':False})
        for j in jobs: j['result']=json.loads(j['result']) if j['result'] else None
        return {'version':VERSION,'capabilities':CAPABILITIES,'stats':stats,'sources':sources,'collections':collections,'collection_max_depth':9,'jobs':jobs,'paused':self.paused(),'data_path':str(self.root),'cameras':cameras,'months':months}
    def _worker(self):
        while not self.stop.is_set():
            if self.paused(): self.wake.wait(.5); self.wake.clear(); continue
            self.watch_tick()
            with self.db() as c: job=c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not job:
                self.schedule_backup(); self.wake.wait(.5); self.wake.clear(); continue
            self.run_job(dict(job))
    def run_job(self,job):
        jid=job['id']; payload=json.loads(job['payload']); self.job_update(jid,state='running',error=None,done=0)
        self._current=jid
        try:
            result=getattr(self,'job_'+job['kind'])(jid,payload)
            self.finish_watch(payload,'; '.join(result.get('errors',[])))
            self.job_update(jid,state='partial' if result.get('errors') else 'done',result=json.dumps(result,ensure_ascii=False),error='\n'.join(result.get('errors',[])) or None)
        except JobCancelled: self.job_update(jid,state='cancelled',error='任务已取消')
        except InterruptedError as e: self.job_update(jid,state='queued',error=str(e))
        except Exception as e:
            self.finish_watch(payload,str(e))
            self.job_update(jid,state='failed',error=str(e)[:2000])
        finally: self._current=None
    def job_import(self,jid,p):
        items=[]; seen=set();file_sources=set()
        for item in p['items']:
            with self.db() as c:source=c.execute('SELECT kind,root FROM sources WHERE id=?',(item['source_id'],)).fetchone()
            if not source:raise ValueError('资料来源已移除')
            if source['kind']=='file':
                if source['root']!=item['path']:raise ValueError('单文件来源路径不一致')
                file_sources.add(item['source_id'])
            for path in ([Path(item['path'])] if p.get('file_guards') is not None or source['kind']=='file' else self.iter_files(item['path'])):
                self.checkpoint(); key=(item['source_id'],str(path))
                if key not in seen: items.append((path,item['source_id'])); seen.add(key)
        if p.get('incremental'): self.reconcile_moves([item for item in items if item[1] not in file_sources])
        self.job_update(jid,total=len(items));errors=[];imported=0;interrupted=[]
        guard=threading.Lock();progress=[0];files=[]
        def work(item):
            nonlocal imported
            try: self.checkpoint()
            except (InterruptedError,JobCancelled) as e:
                with guard: interrupted.append(e)
                return
            try:
                if item[1] in file_sources:
                    from disk_discovery import candidate_guard
                    candidate_guard(item[0])  # regular file, no symlink in any parent
                if p.get('file_guards') is not None:
                    from disk_discovery import validate_candidate
                    validate_candidate(item[0],p['file_guards'][str(item[0])])
                stat=item[0].stat()
                with self.db() as c:
                    cached=c.execute('SELECT f.id,ss.size,ss.mtime_ns FROM files f JOIN scan_state ss ON ss.file_id=f.id WHERE f.source_id=? AND f.origin_path=?',(item[1],str(item[0].resolve()))).fetchone()
                if not (p.get('incremental') and cached and (cached['size'],cached['mtime_ns'])==(stat.st_size,stat.st_mtime_ns)):
                    fid=self.ingest(item[0],item[1],p['mode'])
                    with self.db() as c:c.execute('INSERT OR REPLACE INTO scan_state VALUES(?,?,?)',(fid,stat.st_size,stat.st_mtime_ns))
                if p.get('incremental') and cached: fid=cached['id']
                classification = self.apply_classification(p['classification_plan_id'],str(item[0]),fid) if p.get('classification_plan_id') else None
                receipt=None
                if p.get('receipt'):
                    with self.db() as c: receipt=dict(c.execute('SELECT id,name,sha,source_id FROM files WHERE id=?',(fid,)).fetchone())
                    if classification:receipt['classification']=classification
                with guard:
                    imported+=1
                    if receipt and len(files)<1000:files.append(receipt)
            except Exception as e:
                with guard: errors.append(f'{item[0].name}：{e}')
            with guard:
                progress[0]+=1;done=progress[0]
            if done%5==0 or done==len(items): self.job_update(jid,done=done)
        lanes=max(1,min(self.parallel,len(items)))
        if lanes>1:
            with ThreadPoolExecutor(max_workers=lanes) as pool: list(pool.map(work,items))
        else:
            for item in items: work(item)
        if interrupted: raise interrupted[0]
        with self.db() as c:
            for item in p['items']: c.execute('UPDATE sources SET last_scan=? WHERE id=?',(now(),item['source_id']))
        self.manifest(); return {'imported':imported,'files':files,'errors':errors[:30],'failure_count':len(errors),'classification_batch_id':p.get('classification_plan_id')}
    def job_refresh(self,jid,p):
        with self.db() as c:
            rows=[dict(r) for r in c.execute("SELECT f.id,f.sha,f.name,o.extract_version,m.version AS media_version FROM files f JOIN objects o ON o.sha=f.sha LEFT JOIN media m ON m.sha=f.sha WHERE f.kind IN ('image','video') OR lower(f.name) LIKE '%.html' OR lower(f.name) LIKE '%.htm' OR lower(f.name) LIKE '%.svg'")]
        unique={}
        for row in rows:
            if p.get('force') or row['extract_version']<EXTRACT_VERSION or (kind_for(row['name']) in ('image','video') and (row['media_version'] or 0)<MEDIA_VERSION):unique.setdefault(row['sha'],row)
        self.job_update(jid,total=len(unique));updated=0;errors=[]
        for i,row in enumerate(unique.values()):
            self.checkpoint()
            try:
                f=self.file_row(row['id']);path=self._resolve(f)
                if not path:raise ValueError('原件离线，请连接来源后重试')
                before=path.stat()
                if digest(path)!=f['sha']:raise ValueError('原件已有变化，请重新扫描来源')
                chunks,warning=extract(path,f['name'],cancel=self.stop)
                media_data=build_media(path,f['name'],f['sha'],self.root) if kind_for(f['name']) in ('image','video') else None
                # Re-reading every byte to detect a concurrent edit triples the I/O of a full
                # refresh; size plus mtime nanoseconds catches the same cases on this filesystem.
                after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('文件在处理期间发生变化，请重新扫描')
                with self.lock,self.db() as c:
                    recognized=c.execute('SELECT chunks FROM ocr WHERE sha=?',(f['sha'],)).fetchone()
                    if recognized:chunks += json.loads(recognized[0])
                    c.execute('UPDATE objects SET chunks_json=?,warning=?,extract_version=? WHERE sha=?',(json.dumps(chunks,ensure_ascii=False),warning,EXTRACT_VERSION,f['sha']))
                    if media_data is not None:self._save_media(c,f['sha'],media_data)
                    for ref in c.execute('SELECT id,name FROM files WHERE sha=?',(f['sha'],)).fetchall():
                        c.execute('UPDATE files SET kind=? WHERE id=?',(kind_for(ref['name']),ref['id']));self._index(c,ref['id'])
                self.sidecar(f['sha']);updated+=1
                if media_data and media_data['media_error']:errors.append(f['name']+'：'+media_data['media_error'])
            except Exception as e:errors.append(row['name']+'：'+str(e))
            self.job_update(jid,done=i+1)
        self.manifest();return {'updated':updated,'errors':errors[:30],'failure_count':len(errors)}
    def job_verify(self,jid,p):
        with self.db() as c: rows=[dict(r) for r in c.execute('SELECT DISTINCT o.* FROM objects o JOIN files f ON f.sha=o.sha')]
        self.job_update(jid,total=len(rows)); ok=0; missing=0; damaged=[]
        for i,o in enumerate(rows):
            self.checkpoint()
            with self.db() as c: refs=[dict(r) for r in c.execute('SELECT f.*,o.vault_path,s.mode FROM files f JOIN objects o ON f.sha=o.sha JOIN sources s ON s.id=f.source_id WHERE f.sha=?',(o['sha'],))]
            path=next((x for f in refs if (x:=self._resolve(f))),None)
            state='missing' if path is None else 'ok' if digest(path)==o['sha'] else 'changed'
            if state=='ok': ok+=1
            elif state=='missing': missing+=1
            else: damaged.append(refs[0]['name'])
            with self.db() as c: c.execute('UPDATE objects SET integrity=?,verified_at=? WHERE sha=?',(state,now(),o['sha']))
            self.sidecar(o['sha']); self.job_update(jid,done=i+1)
        return {'verified':ok,'missing':missing,'changed':damaged,'errors':[f'内容与入库记录不一致：{n}' for n in damaged][:30]}
    def job_export(self,jid,p):
        ids=p.get('ids') or []; records=[]; objects={}
        with self.db() as c:
            rows=c.execute('SELECT f.*,o.size,o.vault_path,s.mode,s.name AS source_name FROM files f JOIN objects o ON o.sha=f.sha JOIN sources s ON s.id=f.source_id'+(' WHERE f.id IN ('+','.join('?' for _ in ids)+')' if ids else ''),ids).fetchall()
            # Collections travel with the pack: a shelf is part of how the library is
            # organised, so losing it on export would make the copy less useful than
            # the original.
            shelves={}
            tree={row['id']:row for row in collection_rows(c)}
            for r in c.execute('SELECT file_id,collection_id FROM collection_files'):
                shelves.setdefault(r[0],[]).append(tree[r[1]]['path'])
            for row in rows:
                f=dict(row); path=self._resolve(f)
                if path is None: raise ValueError(f'原件离线，不能生成完整包：{f["name"]}')
                objects[f['sha']]=path
                record={k:json.loads(f[k]) if k=='tags' else f[k] for k in ('id','sha','name','kind','tags','starred','added_at','source_name')}
                record['collection_paths']=sorted(shelves.get(f['id'],[]))
                record['collections']=sorted({path[-1] for path in record['collection_paths']})
                records.append(record)
        if not records: raise ValueError('资料库为空，请先导入文件')
        self.job_update(jid,total=len(objects)); package_id=uuid.uuid4().hex
        destination=self.root/'exports'/f'拾页资料包-{datetime.now().strftime("%Y%m%d-%H%M%S")}-{package_id[:6]}.vaultpack'
        temp=self.root/'tmp'/(package_id+'.partial'); checks={}
        try:
            with zipfile.ZipFile(temp,'w',zipfile.ZIP_STORED,allowZip64=True) as z:
                for i,(sha,path) in enumerate(objects.items()):
                    self.checkpoint(); member='objects/'+sha; h=hashlib.sha256()
                    with path.open('rb') as src,z.open(member,'w',force_zip64=True) as dst:
                        for block in iter(lambda:src.read(1024*1024),b''): self.checkpoint(); dst.write(block); h.update(block)
                    if h.hexdigest()!=sha: raise ValueError(f'文件已变化，导出已取消：{path.name}。请重新扫描来源')
                    checks[member]=sha; self.job_update(jid,done=i+1)
                manifest={'format':'shiyue-vaultpack','schema_version':1,'kind':'full','package_id':package_id,'created_at':now(),'files':records,'objects':len(objects)}
                data=canonical(manifest); z.writestr('manifest.json',data); checks['manifest.json']=hashlib.sha256(data).hexdigest()
                check_data=canonical(checks); z.writestr('checksums.json',check_data); z.writestr('root.sha256',hashlib.sha256(check_data).hexdigest())
            with temp.open('rb') as f: os.fsync(f.fileno())
            os.replace(temp,destination)
        finally:
            if temp.exists(): temp.unlink()
        return {'path':str(destination),'files':len(records),'objects':len(objects),'bytes':destination.stat().st_size}
    def job_mount(self,jid,p):
        archive=Path(p['path']).expanduser().resolve()
        if not archive.is_file(): raise ValueError('资料包不存在')
        staging=Path(tempfile.mkdtemp(dir=self.root/'tmp',prefix='mount-'))
        try:
            with zipfile.ZipFile(archive) as z:
                infos=z.infolist(); names=[x.filename for x in infos]
                if len(names)!=len(set(names)): raise ValueError('资料包存在重复条目')
                if len(names)>1_000_003: raise ValueError('包内条目过多')
                for info in infos:
                    if info.filename not in ('manifest.json','checksums.json','root.sha256') and not re.fullmatch(r'objects/[0-9a-f]{64}',info.filename): raise ValueError('包内包含非法路径或未知条目')
                    if info.file_size > max(info.compress_size,1)*1000: raise ValueError('包内压缩比异常')
                if z.getinfo('manifest.json').file_size>64_000_000 or z.getinfo('checksums.json').file_size>64_000_000 or z.getinfo('root.sha256').file_size>100: raise ValueError('包清单过大')
                check_data=z.read('checksums.json')
                if hashlib.sha256(check_data).hexdigest()!=z.read('root.sha256').decode().strip(): raise ValueError('根校验失败')
                checks=json.loads(check_data)
                if not isinstance(checks,dict) or set(checks)!=(set(names)-{'checksums.json','root.sha256'}): raise ValueError('包校验清单不完整')
                manifest_data=z.read('manifest.json')
                if hashlib.sha256(manifest_data).hexdigest()!=checks.get('manifest.json'): raise ValueError('清单校验失败')
                m=json.loads(manifest_data)
                if m.get('format')!='shiyue-vaultpack' or m.get('schema_version')!=1 or m.get('kind')!='full': raise ValueError('不支持此包格式或版本')
                records=m.get('files'); pid=m.get('package_id')
                if not isinstance(records,list) or not records or not isinstance(pid,str) or not re.fullmatch('[0-9a-f]{32}',pid): raise ValueError('包清单格式错误')
                record_ids=set(); shas=set()
                for f in records:
                    if not isinstance(f,dict) or not re.fullmatch('[0-9a-f]{32}',str(f.get('id',''))) or f['id'] in record_ids: raise ValueError('文件记录 ID 无效或重复')
                    record_ids.add(f['id'])
                    if not re.fullmatch('[0-9a-f]{64}',str(f.get('sha',''))) or checks.get('objects/'+f['sha'])!=f['sha']: raise ValueError('对象校验记录无效')
                    if not isinstance(f.get('name'),str) or not f['name'] or len(f['name'])>1000 or '/' in f['name'] or '\\' in f['name']: raise ValueError('文件名不合法')
                    if not isinstance(f.get('tags'),list) or len(f['tags'])>50 or any(not isinstance(t,str) or len(t)>80 for t in f['tags']): raise ValueError('标签格式不合法')
                    shelves=f.get('collections',[])
                    # Packs written before collections existed simply have no key.
                    if not isinstance(shelves,list) or len(shelves)>500 or any(not isinstance(s,str) or not s.strip() or len(s)>40 for s in shelves): raise ValueError('分类记录格式不合法')
                    paths=f.get('collection_paths')
                    if paths is not None:
                        if not isinstance(paths,list) or len(paths)>500: raise ValueError('分类路径记录格式不合法')
                        f['_collection_paths']=[self._collection_path(path) for path in paths]
                    else:
                        f['_collection_paths']=[[name] for name in shelves]
                    shas.add(f['sha'])
                if {'objects/'+s for s in shas}!={n for n in checks if n.startswith('objects/')}: raise ValueError('包对象集合不一致')
                required=sum(z.getinfo('objects/'+s).file_size for s in shas)
                if shutil.disk_usage(self.root).free < required*2+64_000_000: raise ValueError('磁盘可用空间不足以校验并挂载此包')
                self.job_update(jid,total=len(shas)+len(records)); done=0
                for sha in sorted(shas):
                    self.checkpoint(); h=hashlib.sha256()
                    with z.open('objects/'+sha) as src,(staging/sha).open('wb') as dst:
                        for block in iter(lambda:src.read(1024*1024),b''): self.checkpoint(); h.update(block); dst.write(block)
                    if h.hexdigest()!=sha: raise ValueError('对象校验失败：'+sha[:12])
                    done+=1; self.job_update(jid,done=done)
            # Nothing is registered until every member has passed verification.
            sid=self.add_source(archive,'read-only','pack',archive.stem)
            links=[]
            for f in records:
                self.checkpoint(); fid=self.ingest(staging/f['sha'],sid,'read-only',f['tags'],f.get('starred',0),f['name'],'pack://'+pid+'/'+f['id'])
                if f['_collection_paths']: links.append((fid,f['_collection_paths']))
                done+=1; self.job_update(jid,done=done)
            # A shelf name that already exists here is reused, so mounting a pack
            # adds to "AI资料" instead of creating a look-alike second one.
            if links:
                with self.lock,self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    for fid,paths in links:
                        for path in paths:
                            cid,_=self._ensure_collection_path(c,path)
                            c.execute('INSERT OR IGNORE INTO collection_files(collection_id,file_id,added_at) VALUES(?,?,?)',(cid,fid,now()))
            self.manifest(); return {'files':len(records),'source_id':sid,'verified':True}
        finally: shutil.rmtree(staging,ignore_errors=True)
