"""Portable, unencrypted backup repository with immutable whole-file CAS objects.

Each snapshot owns a compressed consistent database and a checksum manifest.
The existing ZIP restore validator also validates this format through an adapter.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    if not re.fullmatch(r'objects/[0-9a-f]{2}/[0-9a-f]{64}|snapshots/[0-9a-f-]+/(?:backup.json|library.sqlite3.gz)', relative):
        raise ValueError('备份路径无效')
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('备份仓库不能包含符号链接')
    return current


class SizedStream:
    def __init__(self, stream, size):
        self.stream, self.remaining = stream, size

    def read(self, count=-1):
        data = self.stream.read(min(count, self.remaining + 1) if count >= 0 else self.remaining + 1)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError('备份数据超出清单大小')
        return data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()


class RepositoryArchive:
    def __init__(self, snapshot):
        self.snapshot = Path(snapshot)
        if not re.fullmatch(r'[0-9a-f-]+', self.snapshot.name) or self.snapshot.parent.name != 'snapshots':
            raise ValueError('请选择具体的增量备份快照目录')
        self.root = self.snapshot.parent.parent
        marker = self.root / 'shiyue-repository.json'
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1000:
            raise ValueError('备份仓库标识无效')
        if json.loads(marker.read_text()).get('version') != 1:
            raise ValueError('备份仓库版本不受支持')
        path = safe_path(self.root, f'snapshots/{self.snapshot.name}/backup.json')
        if not path.is_file() or path.stat().st_size > 64_000_000:
            raise ValueError('备份清单无效')
        self.metadata = path.read_bytes()
        self.meta = json.loads(self.metadata)
        self.sizes = self.meta.get('sizes', {})
        if not isinstance(self.sizes, dict) or any(type(n) is not int or n < 0 for n in self.sizes.values()):
            raise ValueError('备份大小清单无效')
        if self.sizes.get('library.sqlite3', 0) > 8 * 1024 ** 3:
            raise ValueError('数据库超过恢复上限')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def namelist(self):
        return ['backup.json'] + list(self.sizes)

    def getinfo(self, name):
        return SimpleNamespace(file_size=len(self.metadata) if name == 'backup.json' else self.sizes[name])

    def read(self, name):
        if name != 'backup.json':
            raise ValueError('仅允许读取备份清单')
        return self.metadata

    def open(self, name):
        if name == 'library.sqlite3':
            stream = gzip.open(safe_path(self.root, f'snapshots/{self.snapshot.name}/library.sqlite3.gz'), 'rb')
        else:
            stream = safe_path(self.root, name).open('rb')
        return SizedStream(stream, self.sizes[name])


class RepositoryFeatures:
    def repository_root(self, path):
        if not isinstance(path, str) or not Path(path).expanduser().is_absolute():
            raise ValueError('请选择备份磁盘的绝对路径')
        root = Path(path).expanduser().resolve()
        if root == self.root or self.root in root.parents or root in self.root.parents:
            raise ValueError('增量备份仓库必须放在当前资料库之外')
        with self.db() as c:
            sources = [Path(r[0]).resolve() for r in c.execute("SELECT root FROM sources WHERE kind='folder'")]
        if any(source == root or source in root.parents for source in sources):
            raise ValueError('备份仓库不能放在已索引的来源内，以免反复索引备份')
        if root.exists() and not root.is_dir():
            raise ValueError('备份位置不是文件夹')
        marker = root / 'shiyue-repository.json'
        if root.exists() and any(root.iterdir()) and not marker.is_file():
            raise ValueError('此文件夹已有其他内容，请新建空文件夹保存备份')
        if marker.exists() and (marker.is_symlink() or marker.stat().st_size > 1000 or json.loads(marker.read_text()).get('version') != 1):
            raise ValueError('备份仓库标识无效')
        return root

    def repository_plan(self, path, include_external=False):
        root = self.repository_root(path)
        if type(include_external) is not bool:
            raise ValueError('外部原件选项无效')
        with self.db() as c:
            rows = [dict(r) for r in c.execute('SELECT sha,size,vault_path FROM objects')]
            locations = {}
            for r in c.execute('SELECT sha,origin_path FROM files'):
                locations.setdefault(r['sha'], []).append(r['origin_path'])
        included = [r for r in rows if r['vault_path'] or (include_external and r['sha'] in locations)]
        available = sum(r['size'] for r in included)
        reused = sum(r['size'] for r in included if safe_path(root, f'objects/{r["sha"][:2]}/{r["sha"]}').is_file())
        probe = root
        while not probe.exists():
            probe = probe.parent
        return {'path': str(root), 'objects': len(included), 'total_bytes': available,
                'new_bytes_estimate': available - reused, 'reuse_bytes_estimate': reused,
                'database_bytes': self.dbpath.stat().st_size, 'free_bytes': shutil.disk_usage(probe).free,
                'include_external': include_external, 'same_volume': os.stat(probe).st_dev == os.stat(self.root).st_dev,
                'notice': '复用对象仍逐个校验 SHA-256；只备份与索引一致的原件，缺失或变化会停止发布快照。'}

    def repository_list(self, path):
        root = self.repository_root(path)
        snapshots = root / 'snapshots'
        if snapshots.is_symlink():
            raise ValueError('快照目录不能是符号链接')
        out = []
        if snapshots.exists():
            for p in sorted(snapshots.iterdir(), reverse=True):
                if not p.is_dir() or p.name.startswith('.'):
                    continue
                try:
                    archive = RepositoryArchive(p)
                    out.append({'path': str(p), 'created_at': archive.meta['created_at'],
                                'objects': len(archive.sizes) - 1,
                                'external_objects': archive.meta.get('external_objects', 0)})
                except (OSError, ValueError, KeyError):
                    continue
        return out[:200]

    def job_repository_backup(self, jid, payload):
        from vault import atomic_write
        root = self.repository_root(payload['path'])
        include_external = payload.get('include_external', False)
        if type(include_external) is not bool:
            raise ValueError('外部原件选项无效')
        root.mkdir(parents=True, exist_ok=True)
        marker = root / 'shiyue-repository.json'
        if not marker.exists():
            atomic_write(marker, json.dumps({'format': 'shiyue-repository', 'version': 1}).encode())
        for name in ('objects', 'snapshots'):
            if (root / name).is_symlink():
                raise ValueError('仓库目录不能是符号链接')
            (root / name).mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(dir=root / 'snapshots', prefix='.partial-'))
        identifier = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        checks, sizes, reused, added = {}, {}, 0, 0
        try:
            with self.lock:
                database = stage / 'library.sqlite3'
                with closing(sqlite3.connect(self.dbpath)) as src, closing(sqlite3.connect(database)) as dst:
                    src.backup(dst)
                with closing(sqlite3.connect(database)) as c:
                    c.row_factory = sqlite3.Row
                    objects = [dict(r) for r in c.execute('SELECT sha,size,vault_path FROM objects')]
                    files = [dict(r) for r in c.execute('SELECT id,sha,name,origin_path FROM files')]
                    locations = {}
                    for f in files:
                        locations.setdefault(f['sha'], []).append(f['origin_path'])
                    covered = []
                    for obj in objects:
                        if obj['vault_path'] or (include_external and obj['sha'] in locations):
                            covered.append(obj)
                    self.job_update(jid, total=len(covered))
                    needed = sum(o['size'] for o in covered if not safe_path(root, f'objects/{o["sha"][:2]}/{o["sha"]}').is_file())
                    if shutil.disk_usage(root).free < needed + database.stat().st_size + 32 * 1024 ** 2:
                        raise ValueError('备份磁盘空间不足')
                    for index, obj in enumerate(covered):
                        self.checkpoint()
                        sha = obj['sha']
                        if not re.fullmatch(r'[0-9a-f]{64}', sha):
                            raise ValueError('对象标识无效')
                        rel = f'objects/{sha[:2]}/{sha}'
                        target = safe_path(root, rel)
                        target.parent.mkdir(exist_ok=True)
                        if target.is_file():
                            if sha256(target) != sha:
                                raise ValueError('已有备份对象校验失败，请保留仓库并检查磁盘')
                            reused += target.stat().st_size
                        else:
                            if obj['vault_path']:
                                if obj['vault_path'] != rel:
                                    raise ValueError('托管对象路径无效')
                                source = self.root / rel
                            else:
                                source = next((Path(p) for p in locations.get(sha, []) if Path(p).is_file() and not Path(p).is_symlink()), None)
                            if source is None or source.is_symlink() or not source.is_file():
                                raise ValueError('原件离线，快照未发布；请连接来源后重试')
                            fd, temp = tempfile.mkstemp(dir=target.parent, prefix='.copy-')
                            try:
                                with os.fdopen(fd, 'wb') as dst, source.open('rb') as src:
                                    h = hashlib.sha256()
                                    for block in iter(lambda: src.read(1024 * 1024), b''):
                                        self.checkpoint()
                                        dst.write(block); h.update(block)
                                    dst.flush(); os.fsync(dst.fileno())
                                if h.hexdigest() != sha:
                                    raise ValueError('原件已变化或损坏，请重新扫描后备份；快照未发布')
                                os.replace(temp, target)
                            finally:
                                if os.path.exists(temp):
                                    os.unlink(temp)
                            added += target.stat().st_size
                        checks[rel], sizes[rel] = sha, target.stat().st_size
                        c.execute('UPDATE objects SET vault_path=? WHERE sha=?', (rel, sha))
                        self.job_update(jid, done=index + 1)
                    c.commit(); c.execute('VACUUM')
                    if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall():
                        raise ValueError('备份数据库校验失败')
                    external = [{'sha256': r['sha'], 'indexed_bytes': r['size']} for r in c.execute('SELECT sha,size FROM objects WHERE vault_path IS NULL')]
                    ext_shas = {r['sha256'] for r in external}
                    external_files = [f for f in files if f['sha'] in ext_shas]
                checks['library.sqlite3'], sizes['library.sqlite3'] = sha256(database), database.stat().st_size
                with database.open('rb') as src, gzip.open(stage / 'library.sqlite3.gz', 'wb', compresslevel=6) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                database.unlink()
                metadata = {'format': 'shiyue-backup', 'version': 1, 'repository_version': 1,
                            'created_at': datetime.now(timezone.utc).isoformat(), 'checksums': checks, 'sizes': sizes,
                            'external_objects': len(external), 'external_object_manifest': external,
                            'external_files': external_files, 'include_external': include_external,
                            'database_compacted': True}
                atomic_write(stage / 'backup.json', json.dumps(metadata, ensure_ascii=False).encode())
                with (stage / 'library.sqlite3.gz').open('rb') as stream:
                    os.fsync(stream.fileno())
                destination = root / 'snapshots' / identifier
                os.replace(stage, destination)
            with self.db() as c:
                c.execute("INSERT OR REPLACE INTO settings VALUES('last_repository_backup',?)", (json.dumps({'path': str(destination), 'created_at': metadata['created_at'], 'external_objects': len(external)}),))
            return {'path': str(destination), 'added_bytes': added, 'reused_bytes': reused,
                    'external_objects': len(external), 'objects': len(checks) - 1}
        finally:
            if stage.exists():
                shutil.rmtree(stage)
