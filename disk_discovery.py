"""Bounded, metadata-only local file discovery. Nothing is indexed until selected.

Sessions are ephemeral. Candidate guards are saved with the durable import job;
file identity and every parent directory are checked again before ingestion.
"""
from __future__ import annotations
import os
from pathlib import Path
import stat
import threading
import time
import uuid

MAX_FILES = 50_000
MAX_ENTRIES = 250_000
MAX_SECONDS = 300
MAX_SESSIONS = 3
SKIP_DIRS = {'Library', 'System', 'Applications', 'node_modules', '__pycache__',
             'Caches', '$RECYCLE.BIN', 'System Volume Information', 'lost+found'}
SYSTEM_ROOTS = {'bin', 'sbin', 'usr', 'dev', 'private', 'etc', 'opt'}
BUNDLE_SUFFIXES = ('.app', '.framework', '.bundle', '.photoslibrary', '.photolibrary')
DOC_EXT = {'.txt','.md','.markdown','.csv','.tsv','.json','.yaml','.yml','.log','.html','.htm',
           '.xml','.py','.js','.css','.sql','.rtf','.pdf','.doc','.docx','.xls','.xlsx','.ppt',
           '.pptx','.epub','.odt','.ods','.odp','.pages','.numbers','.key'}
IMAGE_EXT = {'.jpg','.jpeg','.png','.gif','.webp','.heic','.heif','.tif','.tiff','.bmp','.svg'}
VIDEO_EXT = {'.mp4','.mov','.m4v','.avi','.mkv','.webm'}
AUDIO_EXT = {'.mp3','.m4a','.wav','.flac','.aac','.ogg','.aiff'}
KINDS = {'document','image','video','audio','other'}


def _absolute(path):
    if not isinstance(path,(str,Path)) or not str(path) or '\0' in str(path):
        raise ValueError('文件路径无效')
    p = Path(path).expanduser()
    if not p.is_absolute() or '..' in p.parts:
        raise ValueError('请选择完整的本地绝对路径')
    return p


def _open_directory(path, expected=None):
    """Walk from / using dir_fd and O_NOFOLLOW, so no parent symlink is followed."""
    p = _absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    chain = []
    try:
        prefix = Path('/')
        for name in p.parts[1:]:
            next_fd = os.open(name, flags, dir_fd=fd)
            os.close(fd); fd = next_fd; prefix /= name
            metadata = os.fstat(fd)
            chain.append([str(prefix), metadata.st_dev, metadata.st_ino])
        if expected is not None and chain != expected:
            raise ValueError('文件所在目录已变化，请重新扫描或提交')
        return fd, chain
    except BaseException:
        os.close(fd)
        raise


def _fingerprint(value):
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]


def candidate_guard(path, root=None):
    """Take a metadata identity snapshot of an explicit regular file, no reads."""
    path = _absolute(path)
    root = _absolute(root) if root is not None else path.parent
    if path == root or root not in path.parents:
        raise ValueError('文件不在选定目录内')
    try:
        fd, chain = _open_directory(path.parent)
        try:
            metadata = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('只能选择普通文件，不接受目录或符号链接')
            return {'path':str(path), 'root':str(root), 'parents':chain,
                    'stat':_fingerprint(metadata)}
        finally:
            os.close(fd)
    except OSError as error:
        raise ValueError('文件不可读取，或路径中包含符号链接') from error


def validate_candidate(path, guard):
    """Reject replacements/changes between discovery, submission and worker use."""
    if not isinstance(guard,dict):
        raise ValueError('缺少扫描时的文件记录，请重新扫描')
    try:
        path = _absolute(path)
        root = _absolute(guard['root'])
        if str(path) != guard['path'] or root not in path.parents:
            raise ValueError('文件路径与扫描记录不一致')
        fd, _ = _open_directory(path.parent, guard['parents'])
        try:
            metadata = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or _fingerprint(metadata) != guard['stat']:
                raise ValueError('文件已变化，请重新扫描或提交')
        finally:
            os.close(fd)
    except (OSError,KeyError,TypeError) as error:
        raise ValueError('文件或所在目录已变化，请重新扫描或提交') from error


def _kind(path):
    suffix = path.suffix.lower()
    return ('document' if suffix in DOC_EXT else 'image' if suffix in IMAGE_EXT else
            'video' if suffix in VIDEO_EXT else 'audio' if suffix in AUDIO_EXT else 'other')


class DiscoveryService:
    def __init__(self, vault):
        self.vault = vault
        self._lock = threading.RLock()
        self._sessions = {}
        self._closed = False

    def locations(self):
        home = Path.home()
        locations = []
        for name, path in [('桌面',home/'Desktop'),('文稿',home/'Documents'),
                           ('下载',home/'Downloads'),('个人目录',home)]:
            if path.is_dir(): locations.append({'name':name,'path':str(path.resolve()),'kind':'folder'})
        try:
            for path in sorted(Path('/Volumes').iterdir()):
                if path.is_dir() and not path.is_symlink() and not path.name.startswith('.'):
                    locations.append({'name':path.name,'path':str(path.resolve()),'kind':'volume'})
        except OSError:
            pass
        return {'locations':locations,'defaults':[x['path'] for x in locations[:3]],
                'notice':'只扫描所选位置的名称、类型、大小和修改时间。隐藏目录、系统与缓存、应用包和符号链接会跳过；不会跨卷扫描。勾选文件并确认后才建立索引。'}

    def start(self, paths, max_files=MAX_FILES):
        if not isinstance(paths,list) or not 1 <= len(paths) <= 12:
            raise ValueError('请选择 1–12 个扫描目录或磁盘')
        if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
            raise ValueError('候选文件上限应为 1–50000')
        roots = []
        for raw in paths:
            initial = _absolute(raw)
            if initial.is_symlink(): raise ValueError('请选择实际目录，不跟随符号链接')
            try:
                root = initial.resolve(strict=True)
                if not root.is_dir(): raise ValueError('扫描位置必须是文件夹或磁盘')
                if root == self.vault.root or self.vault.root in root.parents:
                    raise ValueError('不能扫描资料库自身目录')
                fd, chain = _open_directory(root)
                try: metadata = os.fstat(fd)
                finally: os.close(fd)
            except OSError as error:
                raise ValueError('无法访问扫描位置，请检查目录与权限') from error
            if any((root == r[0] or r[0] in root.parents) and metadata.st_dev==r[2] for r in roots): continue
            roots = [r for r in roots if root not in r[0].parents or metadata.st_dev!=r[2]]
            roots.append((root,chain,metadata.st_dev))
        with self._lock:
            if self._closed: raise ValueError('扫描服务已关闭')
            if any(s['state']=='running' for s in self._sessions.values()):
                raise ValueError('已有扫描正在进行，请先停止或等待完成')
            while len(self._sessions)>=MAX_SESSIONS:
                self._sessions.pop(next(iter(self._sessions)))
            session = {'id':uuid.uuid4().hex,'state':'running','roots':[str(r[0]) for r in roots],
                       'started_at':time.time(),'finished_at':None,'visited':0,'directories':0,
                       'skipped':0,'errors':0,'error_samples':[],'limited_reason':'',
                       'max_files':max_files,'max_entries':MAX_ENTRIES,'max_seconds':MAX_SECONDS,
                       'counts':dict.fromkeys(sorted(KINDS),0),'bytes':0,'items':{},
                       'cancel':threading.Event(),'submissions':{},'thread':None}
            self._sessions[session['id']] = session
            thread = threading.Thread(target=self._scan,args=(session,roots),daemon=True,
                                      name='shiyue-discovery-'+session['id'][:8])
            session['thread'] = thread
            thread.start()
            return self._summary(session)

    def _summary(self, session):
        names=('id','state','roots','started_at','finished_at','visited','directories','skipped',
               'errors','error_samples','limited_reason','max_files','max_entries','max_seconds','bytes')
        data = {key:session[key] for key in names}
        data['error_samples'] = list(session['error_samples'])
        data['counts'] = dict(session['counts'])
        data['total'] = len(session['items'])
        data['ephemeral'] = True
        return data

    def _error(self, session, path, error):
        with self._lock:
            session['errors'] += 1
            if len(session['error_samples'])<8:
                session['error_samples'].append({'path':str(path),'reason':str(error)[:200]})

    def _scan(self, session, roots):
        started = time.monotonic()
        try:
            with self.vault.db() as c:
                indexed = {r[0] for r in c.execute('SELECT origin_path FROM files')}
            stack = list(reversed(roots))
            seen_dirs = set()
            while stack:
                if session['cancel'].is_set(): break
                if time.monotonic()-started >= session['max_seconds']:
                    session['limited_reason']='达到扫描时间上限，请缩小目录范围后继续';break
                path, parents, device = stack.pop()
                try:
                    fd, chain = _open_directory(path, parents)
                    try:
                        metadata = os.fstat(fd)
                        identity = (metadata.st_dev,metadata.st_ino)
                        if metadata.st_dev != device or identity in seen_dirs:
                            with self._lock: session['skipped']+=1
                            continue
                        seen_dirs.add(identity)
                        with self._lock: session['directories']+=1
                        with os.scandir(fd) as entries:
                            for entry in entries:
                                if session['cancel'].is_set(): break
                                if time.monotonic()-started >= session['max_seconds']:
                                    session['limited_reason']='达到扫描时间上限，请缩小目录范围后继续';break
                                if session['visited']>=session['max_entries']:
                                    session['limited_reason']='达到目录项上限，请缩小目录范围后继续';break
                                with self._lock: session['visited']+=1
                                candidate = path/entry.name
                                if entry.name.startswith('.'):
                                    with self._lock: session['skipped']+=1
                                    continue
                                try:
                                    value = entry.stat(follow_symlinks=False)
                                    if stat.S_ISLNK(value.st_mode) or value.st_dev != device:
                                        with self._lock: session['skipped']+=1
                                        continue
                                    if stat.S_ISDIR(value.st_mode):
                                        if (entry.name in SKIP_DIRS or
                                            (path==Path('/') and entry.name in SYSTEM_ROOTS) or
                                            entry.name.lower().endswith(BUNDLE_SUFFIXES) or
                                            candidate==self.vault.root or self.vault.root in candidate.parents or
                                            len(candidate.parts)>64):
                                            with self._lock: session['skipped']+=1
                                            continue
                                        stack.append((candidate,chain+[[str(candidate),value.st_dev,value.st_ino]],device))
                                    elif stat.S_ISREG(value.st_mode):
                                        if len(session['items'])>=session['max_files']:
                                            session['limited_reason']='达到候选文件上限，请缩小目录范围后继续';break
                                        kind = _kind(candidate); item_id = uuid.uuid4().hex
                                        root = max((r[0] for r in roots if r[0] in candidate.parents and r[2]==device),key=lambda p:len(p.parts))
                                        guard = {'path':str(candidate),'root':str(root),'parents':chain,
                                                 'stat':_fingerprint(value)}
                                        item = {'id':item_id,'name':entry.name,'path':str(candidate),'kind':kind,
                                                'size':value.st_size,'mtime_ns':value.st_mtime_ns,
                                                'indexed':str(candidate) in indexed,'guard':guard}
                                        with self._lock:
                                            session['items'][item_id]=item
                                            session['counts'][kind]+=1;session['bytes']+=value.st_size
                                except OSError as error:
                                    self._error(session,candidate,error)
                    finally:
                        os.close(fd)
                except (OSError,ValueError) as error:
                    self._error(session,path,error)
                if session['limited_reason']: break
            with self._lock:
                session['state'] = ('cancelled' if session['cancel'].is_set() else
                                    'limited' if session['limited_reason'] else 'done')
        except Exception as error:
            self._error(session,'',error)
            with self._lock: session['state']='failed'
        finally:
            with self._lock: session['finished_at']=time.time()

    def _session(self, scan_id):
        session = self._sessions.get(scan_id) if isinstance(scan_id,str) else None
        if session is None: raise ValueError('扫描记录不存在或已过期，请重新扫描')
        return session

    def status(self, scan_id, q='', kind='', offset=0, limit=100):
        if not isinstance(q,str) or len(q)>200 or kind not in KINDS|{''}:
            raise ValueError('筛选条件无效')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=200:
            raise ValueError('分页参数无效')
        query=q.casefold().strip()
        with self._lock:
            session=self._session(scan_id); result=self._summary(session)
            items=[v for v in session['items'].values() if (not kind or v['kind']==kind) and
                   (not query or query in v['path'].casefold())]
            result.update(filtered_total=len(items),offset=offset,limit=limit,has_more=offset+limit<len(items),
                          items=[{k:v for k,v in item.items() if k!='guard'} for item in items[offset:offset+limit]])
            return result

    def cancel(self, scan_id):
        with self._lock:
            session=self._session(scan_id);session['cancel'].set()
            return self._summary(session)

    def preview(self, scan_id, ids):
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(i,str) for i in ids):
            raise ValueError('请勾选 1–1000 个文件')
        with self._lock:
            session=self._session(scan_id)
            if session['state']=='running': raise ValueError('请等待扫描结束或停止扫描后再预览')
            if any(i not in session['items'] for i in ids): raise ValueError('选择的文件不属于此次扫描')
            selected=[session['items'][i] for i in sorted(set(ids))]
            for item in selected:validate_candidate(item['path'],item['guard'])
            return self.vault.preview_classification(scan_id,selected)

    def submit(self, scan_id, ids, mode='in-place', request_id=None, plan_id=None):
        if mode not in ('managed','in-place'): raise ValueError('请选择托管复制或就地索引')
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(i,str) for i in ids):
            raise ValueError('请勾选 1–1000 个文件')
        ids=sorted(set(ids))
        with self._lock:
            session=self._session(scan_id)
            if session['state']=='running': raise ValueError('请等待扫描结束或停止扫描后再加入')
            if any(i not in session['items'] for i in ids): raise ValueError('选择的文件不属于此次扫描')
            # The same selection is naturally idempotent. Explicit IDs also reject
            # different selections rather than silently creating another job.
            key=uuid.uuid5(uuid.NAMESPACE_URL,scan_id+':'+mode+':'+','.join(ids)).hex if request_id is None else request_id
            if not isinstance(key,str) or not 1<=len(key)<=100: raise ValueError('请求标识无效')
            previous=session['submissions'].get(key)
            signature=(ids,mode,plan_id)
            if previous:
                if previous[0]!=signature: raise ValueError('同一请求标识不能用于不同选择')
                return dict(previous[1],reused=True)
            selected=[session['items'][i] for i in ids]
            for item in selected: validate_candidate(item['path'],item['guard'])
            paths=[item['path'] for item in selected]
            receipt=self.vault.submit_import(paths,mode,'discovery:'+scan_id+':'+key,
                                            file_guards={item['path']:item['guard'] for item in selected},classification_plan_id=plan_id)
            receipt={**receipt,'selected':len(paths),'scan_id':scan_id}
            session['submissions'][key]=(signature,receipt)
            return receipt

    def close(self):
        with self._lock:
            self._closed=True; sessions=list(self._sessions.values())
            for session in sessions:session['cancel'].set()
        for session in sessions:
            session['thread'].join(timeout=2)
