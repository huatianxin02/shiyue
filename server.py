#!/usr/bin/env python3
from __future__ import annotations
import argparse, fcntl, json, re, mimetypes, os, secrets, shutil, signal, subprocess, sys, threading, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from vault import Vault, atomic_write, TEXT_EXT, IMAGE_EXT, VIDEO_EXT
from safe_content import html_preview
from knowledge_mcp import Knowledge
from knowledge_filters import http_filters
import agent_catalog
import agent_ingest
from disk_discovery import DiscoveryService

BASE=Path(__file__).resolve().parent

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,vault):
        super().__init__(address,Handler); self.vault=vault; self.token=secrets.token_urlsafe(32)
        self.media_token=secrets.token_urlsafe(32)
        self.origin=f'http://127.0.0.1:{self.server_port}'
        self.discovery=DiscoveryService(vault)

    def server_close(self):
        self.discovery.close()
        super().server_close()

class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        if hasattr(self.connection,'settimeout'):self.connection.settimeout(15)
    def log_message(self,*args): pass
    def send(self,data,status=200,mime='application/json; charset=utf-8',extra=None):
        if isinstance(data,(dict,list)): data=json.dumps(data,ensure_ascii=False).encode()
        elif isinstance(data,str): data=data.encode()
        extra=extra or {}
        self.send_response(status); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control',extra.get('Cache-Control','no-store')); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        if "Content-Security-Policy" not in extra:self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k,v in extra.items():
            if k!='Cache-Control': self.send_header(k,v)
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(data)
    def guard(self,api=False):
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': raise PermissionError('请求地址无效')
        origin=self.headers.get('Origin')
        if origin and origin!=self.server.origin: raise PermissionError('不允许跨站访问')
        if self.headers.get('Sec-Fetch-Site')=='cross-site':raise PermissionError('不允许跨站访问')
        if api:
            token=self.headers.get('X-Vault-Token','')
            if secrets.compare_digest(token,self.server.token):return
            # Elements such as img/video/iframe cannot send a custom header. Their
            # separate HttpOnly cookie grants only these read-only representations.
            path=urllib.parse.urlsplit(self.path).path
            if self.command in ('GET','HEAD') and re.fullmatch(r'/api/files/[0-9a-f]{32}/(?:thumbnail|content|page|stream|document)',path):
                cookie=SimpleCookie()
                try:cookie.load(self.headers.get('Cookie',''))
                except Exception:cookie=SimpleCookie()
                media=cookie.get('shiyue_media');expected=getattr(self.server,'media_token','')
                if media and expected and secrets.compare_digest(media.value,expected):return
            raise PermissionError('会话无效，请重新打开应用')
    def media_cookie(self):
        return {'Set-Cookie':f'shiyue_media={self.server.media_token}; HttpOnly; SameSite=Strict; Path=/api/files/'}
    def do_GET(self):
        try:
            url=urllib.parse.urlsplit(self.path); path=url.path; self.guard(path.startswith('/api/'))
            v=self.server.vault; q={k:values[0] for k,values in urllib.parse.parse_qs(url.query).items()}
            if path.startswith('/api/agent/'):
                if path.endswith('/download'): return self.send(agent_catalog.call(path[11:],q=q,binary=True),mime='application/octet-stream',extra={'Content-Disposition':'attachment'})
                return self.send(agent_catalog.call(path[11:],q=q))
            if path=='/api/classification/rules':return self.send({'rules':v.classification_rules()})
            if path=='/api/classification/batch':return self.send(v.classification_batch(q.get('id','')))
            if path=='/api/classification/batches':return self.send({'batches':v.classification_batches()})
            if path=='/api/discovery/locations':return self.send(self.server.discovery.locations())
            if path=='/api/discovery/scan':return self.send(self.server.discovery.status(q.get('id',''),q=q.get('q',''),kind=q.get('kind',''),offset=int(q.get('offset',0)),limit=int(q.get('limit',100))))
            if path=='/api/ingest/schema':return self.send(agent_ingest.schema())
            if path=='/api/ingest/config':return self.send({'command':sys.executable,'script':str(BASE/'agent_ingest.py'),'data':str(v.root)})
            if path.startswith('/api/ingest/jobs/'):return self.send(agent_ingest.receipt(v,path[len('/api/ingest/jobs/'):]))
            if path.startswith('/api/jobs/'): return self.send(v.job_receipt(path[10:]))
            if path=='/api/knowledge/recent': return self.send(Knowledge(v.root,q.get('profile','default')).recent())
            if path=='/api/knowledge/profiles': return self.send(v.knowledge_profiles())
            if path=='/api/note': return self.send(v.note(q.get('id','')))
            if path=='/api/citation/read': return self.send(Knowledge(v.root,None).read(q.get('id',''),int(q.get('start','0')),1,q.get('expected_sha'),q.get('expected_extract')))
            if path=='/api/citation': return self.send(Knowledge(v.root,None).passage(q.get('id',''),q.get('passage',''),1))
            if path=='/api/knowledge/passage': return self.send(Knowledge(v.root,q.get('profile','default')).passage(q.get('id',''),q.get('passage',''),int(q.get('context','1'))))
            if path=='/api/knowledge/info': return self.send(Knowledge(v.root,q.get('profile','default')).info())
            if path=='/api/knowledge/search': return self.send(Knowledge(v.root,q.get('profile','default')).search(q.get('q',''),int(q.get('limit','8')),**http_filters(q)))
            if path=='/api/knowledge/read': return self.send(Knowledge(v.root,q.get('profile','default')).read(q.get('id',''),int(q.get('start','0')),int(q.get('limit','6')),q.get('expected_sha'),q.get('expected_extract')))
            if path=='/api/knowledge/config': return self.send({'mcpServers':{'shiyue':{'command':sys.executable,'args':['-B',str(BASE/'knowledge_mcp.py'),'--profile',q.get('profile','default'),'--data',str(v.root)]}}})
            if path=='/api/collections': return self.send({'collections':v.collections(),'max_depth':9})
            if path=='/api/status': return self.send(v.status())
            if path=='/api/health': return self.send({**v.health(),'knowledge':v.knowledge_health()})
            if path=='/api/trash': return self.send(v.trash_list())
            if path=='/api/repository/list': return self.send(v.repository_list(q.get('path','')))
            if path=='/api/backups': return self.send(v.backup_list())
            if path=='/api/searches': return self.send(v.saved_searches())
            if path=='/api/files': return self.send(v.list_files(q.get('q',''),q.get('kind',''),q.get('source',''),q.get('starred')=='1',int(q.get('offset',0)),sort=q.get('sort','added'),camera=q.get('camera',''),month=q.get('month',''),collection=q.get('collection',''),tag=q.get('tag',''),after=q.get('after',''),before=q.get('before',''),include_children=q.get('include_children','1')!='0'))
            if path.startswith('/api/files/'):
                rest=path[len('/api/files/'):].split('/'); fid=rest[0]
                if len(rest)>1 and rest[1]=='reader': return self.send({**v.document_probe(fid),**v.reading_position(fid)})
                if len(rest)>1 and rest[1]=='page':
                    p=v.document_probe(fid,'page',int(q.get('page',1)))
                    return self.send(p.read_bytes(),mime='image/jpeg',extra={'Cache-Control':'private, max-age=86400'})
                if len(rest)==1: return self.send(v.detail(fid))
                if rest[1] in ('content','thumbnail'):
                    p=v.thumbnail(fid)
                    mime='image/svg+xml' if p.suffix=='.svg' else 'image/jpeg'
                    # Derivatives are named after the content hash, so a given URL can never
                    # change. Without this a photo grid re-downloads every tile while scrolling.
                    return self.send(p.read_bytes(),mime=mime,extra={'Cache-Control':'private, max-age=31536000, immutable','Content-Security-Policy':"default-src 'none'; sandbox; frame-ancestors 'self'"})
                if rest[1]=='document':
                    f=v.file_row(fid);p=v._resolve(f)
                    if not p:raise ValueError('原件离线，仍可查看已提取的文字')
                    if Path(f['name']).suffix.lower() not in ('.html','.htm'):raise ValueError('该文件不是网页存档')
                    with p.open('rb') as source:data=source.read(8_000_001)
                    if len(data)>8_000_000:raise ValueError('网页大于 8 MB，请查看原文片段或定位文件')
                    return self.send(html_preview(data,f['name']),mime='text/html; charset=utf-8',extra={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; sandbox; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"})
                if rest[1]=='stream':
                    f=v.file_row(fid);p=v._resolve(f)
                    if f['kind']!='video' or Path(f['name']).suffix.lower() not in ('.mp4','.mov','.m4v','.webm'):raise ValueError('此视频容器请用外部播放器打开')
                    if not p:raise ValueError('原件离线，请连接来源后播放')
                    return self.stream(p,f['mime'])
            if path=='/integration.html':
                policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                return self.send((BASE/'web/integration.html').read_text().replace('__VAULT_TOKEN__',self.server.token),mime='text/html; charset=utf-8',extra={'Content-Security-Policy':policy,**self.media_cookie()})
            if path in ('/','/index.html'):
                html=(BASE/'web/index.html').read_text().replace('__VAULT_TOKEN__',self.server.token)
                return self.send(html,mime='text/html; charset=utf-8',extra=self.media_cookie())
            if path in ('/organization.js','/app.js','/features.js','/discovery.js','/style.css','/integration.js','/integration.css','/artifact-preview.js','/joint-backup.js'):
                p=BASE/'web'/path[1:]; return self.send(p.read_bytes(),mime='text/javascript; charset=utf-8' if p.suffix=='.js' else 'text/css; charset=utf-8')
            self.send({'error':'页面不存在'},404)
        except PermissionError as e: self.send({'error':str(e)},403)
        except (ValueError,KeyError) as e: self.send({'error':str(e)},400)
        except BrokenPipeError: pass
        except Exception as e: self.send({'error':str(e)},500)
    def do_HEAD(self):self.do_GET()
    def stream(self,path,mime):
        # Seek and stream bounded chunks; never load a video into memory.
        with path.open('rb') as source:
            size=os.fstat(source.fileno()).st_size;start=0;end=size-1;status=200
            value=self.headers.get('Range')
            if value:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',value) if len(value)<200 else None
                valid=bool(match and (match[1] or match[2]))
                if valid:
                    if not match[1]:
                        suffix=int(match[2]);valid=suffix>0;start=max(0,size-suffix)
                    else:
                        start=int(match[1]);end=min(size-1,int(match[2])) if match[2] else size-1
                    valid=valid and size>0 and start<size and end>=start
                if not valid:return self.send({'error':'请求的片段范围无效'},416,extra={'Content-Range':f'bytes */{size}','Accept-Ranges':'bytes'})
                status=206
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(max(0,end-start+1)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command=='HEAD':return
            source.seek(start);remaining=end-start+1
            while remaining>0:
                block=source.read(min(256*1024,remaining))
                if not block:break
                self.wfile.write(block);remaining-=len(block)
    def do_POST(self):
        try:
            self.guard(True)
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))>1:raise ValueError('不支持该请求长度格式')
            if not self.headers.get('Content-Type','').startswith('application/json'): raise ValueError('请求格式必须为 JSON')
            length=int(self.headers.get('Content-Length',0))
            if length<0 or length>2_000_000: raise ValueError('请求过大')
            raw=self.rfile.read(length)
            if len(raw)!=length:raise ValueError('请求内容不完整')
            p=json.loads(raw or '{}'); path=urllib.parse.urlsplit(self.path).path; v=self.server.vault
            if not isinstance(p,dict):raise ValueError('请求必须为 JSON 对象')
            if path.startswith('/api/agent/'):
                return self.send(agent_catalog.call(path[11:],data=p))
            if path=='/api/discovery/start':result=self.server.discovery.start(p.get('paths',[]))
            elif path=='/api/discovery/cancel':result=self.server.discovery.cancel(p.get('id',''))
            elif path=='/api/discovery/import':result=self.server.discovery.submit(p.get('id',''),p.get('ids',[]),p.get('mode','in-place'),p.get('request_id'),p.get('plan_id'))
            elif path=='/api/discovery/preview':result=self.server.discovery.preview(p.get('id',''),p.get('ids',[]))
            elif path=='/api/classification/rule':result=v.save_classification_rule(**p)
            elif path=='/api/classification/delete':result=v.delete_classification_rule(**p)
            elif path=='/api/classification/undo':result=v.undo_classification(**p)
            elif path=='/api/files/preference':result=v.set_preference(**p)
            elif path=='/api/ingest/files':result=agent_ingest.submit(v,p)
            elif path=='/api/integration/scope': result=agent_catalog.ensure_scope(v)
            elif path=='/api/import': result=v.submit_import(p.get('paths',[]),p.get('mode','managed'),p.get('request_id'))
            elif path=='/api/pause': v.set_pause(bool(p.get('paused'))); result={'ok':True}
            elif path=='/api/refresh': result=v.queue('refresh',{'force':True})
            elif path=='/api/verify': result=v.queue('verify',{})
            elif path=='/api/export': result=v.queue('export',{'ids':p.get('ids',[])})
            elif path=='/api/mount': result=v.queue('mount',{'path':p['path']})
            elif path=='/api/knowledge/rebuild': result=v.queue('rebuild_knowledge',{})
            elif path=='/api/knowledge/profile': result=v.save_knowledge_profile(**p)
            elif path=='/api/note': result=v.note(p['id'],p['body'],p.get('expected_updated_at'))
            elif path=='/api/source/watch': result=v.set_watch(p['source_id'],p['enabled'])
            elif path=='/api/rescan': result=v.rescan(p['source_id'],p.get('incremental',False))
            elif path=='/api/source/impact': result=v.source_impact(p['source_id'])
            elif path=='/api/source/remove': result=v.trash_source(p['source_id'])
            elif path=='/api/file/impact': result=v.file_impact(p.get('ids',[p['id']] if 'id' in p else []))
            elif path=='/api/file/remove': result=v.trash_files(p.get('ids',[p['id']] if 'id' in p else []))
            elif path=='/api/job/delete': result=v.delete_job(p['job_id'])
            elif path=='/api/collection/create': result=v.create_collection(p.get('name'),p.get('parent_id'))
            elif path=='/api/collection/rename': result=v.rename_collection(p['collection_id'],p.get('name'))
            elif path=='/api/collection/move': result=v.move_collection(p['collection_id'],p.get('parent_id'),p.get('target_id'),p.get('position'))
            elif path=='/api/collection/delete': result=v.delete_collection(p['collection_id'])
            elif path=='/api/collection/assign': result=v.assign_collection(p.get('collection_id'),p.get('ids',[]),p.get('action','add'),p.get('name'),p.get('parent_id'),p.get('path'))
            elif path=='/api/retry': v.retry(p['job_id']); result={'ok':True}
            elif path=='/api/trash/restore': result=v.restore_trash(p['id'])
            elif path=='/api/trash/purge': result=v.purge_trash(p['id'])
            elif path=='/api/repository/plan': result=v.repository_plan(p['path'],p.get('include_external',False))
            elif path=='/api/repository/backup':
                v.repository_plan(p['path'],p.get('include_external',False))
                result=v.queue('repository_backup',{'path':p['path'],'include_external':p.get('include_external',False)})
            elif path=='/api/backup': result=v.queue('backup',{})
            elif path=='/api/backup/restore': result=v.restore_backup(p['path'])
            elif path=='/api/backup/settings':
                with v.db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('auto_backup',?)",('1' if p.get('enabled') else '0',))
                result={'ok':True}
            elif path=='/api/source/relocate': result=v.relocate_source(p['source_id'],p['path'],bool(p.get('apply')))
            elif path=='/api/search/save': result=v.save_search(p['name'],p['filters'])
            elif path=='/api/search/delete':
                with v.db() as c:c.execute('DELETE FROM saved_searches WHERE id=?',(p['id'],))
                result={'ok':True}
            elif path=='/api/reading': result=v.reading_position(p['id'],p['page'])
            elif path=='/api/ocr': result=v.queue('ocr',{'id':p['id'],'force':bool(p.get('force'))})
            elif path=='/api/edit': result=v.edit(p['id'],p.get('tags'),p.get('starred'))
            elif path=='/api/open':
                f=v.file_row(p['id']); target,kind=v.open_target(f)
                if not target: raise ValueError('原件离线，请先连接来源')
                ext=Path(f['name']).suffix.lower()
                if not p.get('reveal') and ext in ('.html','.htm','.svg'):
                    route='thumbnail' if ext=='.svg' else 'document'
                    return self.send({'preview_url':f'/api/files/{f["id"]}/{route}','name':f['name']})
                # Reveal and open go to the user's own file. Only when it is gone do
                # we fall back to the managed object, and then it is staged under a
                # readable name so the editor never writes into the object store.
                if p.get('reveal'):
                    subprocess.Popen(['/usr/bin/open','-R',str(target)])
                else:
                    if kind=='copy':
                        view=v.root/'tmp'/'open'/f['sha']; view.mkdir(parents=True,exist_ok=True)
                        name=Path(f['name']).name
                        if not name or name in ('.','..'): raise ValueError('文件名无效')
                        staged=view/name; shutil.copy2(target,staged); target=staged
                    if ext in TEXT_EXT or ext in {'.sh','.command','.bash','.zsh','.scpt','.applescript','.svg'}:
                        subprocess.Popen(['/usr/bin/open','-a','TextEdit',str(target)])
                    elif ext in IMAGE_EXT | VIDEO_EXT | {'.pdf','.doc','.docx','.xls','.xlsx','.ppt','.pptx','.pages','.numbers','.key','.mp3','.wav','.m4a'}:
                        subprocess.Popen(['/usr/bin/open',str(target)])
                    else:
                        subprocess.Popen(['/usr/bin/open','-R',str(target)])
                result={'ok':True,'opened':kind,'path':str(target)}
            elif path=='/api/reveal':
                allowed={'data':v.root,'exports':v.root/'exports','manifest':v.root/'manifest.csv'}
                target=allowed.get(p.get('target'))
                if not target: raise ValueError('无效目录')
                if not target.exists(): raise ValueError('请先导入文件以生成清单')
                subprocess.Popen(['/usr/bin/open',str(target)]); result={'ok':True}
            else: return self.send({'error':'操作不存在'},404)
            self.send(result)
        except PermissionError as e: self.send({'error':str(e)},403)
        except (ValueError,KeyError,TypeError) as e: self.send({'error':str(e)},400)
        except BrokenPipeError: pass
        except Exception as e: self.send({'error':str(e)},500)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--data',default=str(BASE/'data')); parser.add_argument('--port',type=int,default=0); parser.add_argument('--ready'); args=parser.parse_args()
    data=Path(args.data).resolve(); data.mkdir(parents=True,exist_ok=True)
    lock=(data/'.instance.lock').open('w')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('资料库已在另一个应用实例中打开。')
    vault=Vault(data); server=Server(('127.0.0.1',args.port),vault)
    if args.ready: atomic_write(args.ready,json.dumps({'url':server.origin,'pid':os.getpid()}).encode())
    descriptor=data/'runtime.json'
    atomic_write(descriptor,json.dumps({'origin':server.origin,'token':server.token,'pid':os.getpid()}).encode())
    descriptor.chmod(0o600)
    print(server.origin,flush=True)
    def shutdown(*_): vault.stop.set(); threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown); signal.signal(signal.SIGINT,shutdown)
    try: server.serve_forever(poll_interval=.2)
    finally:
        server.server_close(); vault.close(); lock.close()
        try:
            if json.loads(descriptor.read_text()).get("pid")==os.getpid():descriptor.unlink()
        except (OSError,ValueError):pass

if __name__=='__main__': main()
