"""HTTP handler tests use byte streams: no browser or network access required."""
import io, json, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Handler
from vault import Vault

class FakeSocket:
    def __init__(self,raw):self.incoming=io.BytesIO(raw);self.outgoing=io.BytesIO()
    def makefile(self,*_):return self.incoming
    def sendall(self,data):self.outgoing.write(data)

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.v=Vault(self.root/'data',start_worker=False)
        self.server=SimpleNamespace(server_port=12345,origin='http://127.0.0.1:12345',token='test-token',media_token='test-media-cookie',vault=self.v)
    def tearDown(self):self.v.close();self.temp.cleanup()
    def request(self,path,body=None,token='test-token',origin=None,host='127.0.0.1:12345'):
        payload=json.dumps(body).encode() if body is not None else b''
        method='POST' if body is not None else 'GET'
        headers=[f'{method} {path} HTTP/1.1',f'Host: {host}','Connection: close']
        if token is not None:headers.append('X-Vault-Token: '+token)
        if origin is not None:headers.append('Origin: '+origin)
        if body is not None:headers.extend(['Content-Type: application/json','Content-Length: '+str(len(payload))])
        sock=FakeSocket(('\r\n'.join(headers)+'\r\n\r\n').encode()+payload)
        Handler(sock,('127.0.0.1',999),self.server)
        raw=sock.outgoing.getvalue();head,result=raw.split(b'\r\n\r\n',1)
        return int(head.split()[1]),head.decode(),result
    def test_requires_token(self):
        for token in (None,'wrong'):
            code,_,_=self.request('/api/status',token=token);self.assertEqual(code,403)
    def test_blocks_foreign_origin_and_host(self):
        code,_,_=self.request('/api/verify',{},origin='https://untrusted.example');self.assertEqual(code,403)
        code,_,_=self.request('/api/status',host='untrusted.example:12345');self.assertEqual(code,403)
    def test_own_origin_allowed(self):
        code,_,body=self.request('/api/status',origin=self.server.origin);self.assertEqual(code,200);self.assertEqual(json.loads(body)['stats']['files'],0)
    def test_html_and_assets(self):
        code,headers,body=self.request('/',token=None);self.assertEqual(code,200);self.assertIn(b'test-token',body);self.assertNotIn(b'__VAULT_TOKEN__',body);self.assertIn('frame-ancestors',headers)
        for path in ['/app.js','/style.css']:
            code,_,body=self.request(path);self.assertEqual(code,200);self.assertTrue(body)
    def test_file_routes_do_not_read_arbitrary_paths(self):
        code,_,_=self.request('/../../etc/passwd');self.assertEqual(code,404)
        code,_,_=self.request('/api/files/not-a-file');self.assertEqual(code,400)
    def test_import_search_edit_export_mount(self):
        path=self.root/'sample.txt';path.write_text('本地资料库接口恢复测试，付款期限三十天。')
        code,_,body=self.request('/api/import',{'paths':[str(path)],'mode':'managed'});self.assertEqual(code,200)
        jid=json.loads(body)['job_id']
        with self.v.db() as c:job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        self.v.run_job(job)
        from urllib.parse import quote
        code,_,body=self.request('/api/files?q='+quote('付款期限'));self.assertEqual(code,200);files=json.loads(body)['files'];self.assertEqual(len(files),1)
        fid=files[0]['id'];code,_,body=self.request('/api/edit',{'id':fid,'tags':['重要'],'starred':True});self.assertEqual(code,200);self.assertEqual(json.loads(body)['tags'],['重要'])
        code,_,body=self.request('/api/export',{'ids':[fid]});self.assertEqual(code,200);jid=json.loads(body)['job_id']
        with self.v.db() as c:job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        self.v.run_job(job)
        job=self.v.status()['jobs'][0];self.assertEqual(job['state'],'done');self.assertTrue(Path(job['result']['path']).exists())
        code,_,body=self.request('/api/mount',{'path':job['result']['path']});self.assertEqual(code,200)
        with self.v.db() as c:job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(json.loads(body)['job_id'],)).fetchone())
        self.v.run_job(job);self.assertEqual(self.v.status()['stats']['files'],2)
    def test_active_content_not_served(self):
        p=self.root/'sample.html';p.write_text('<script>alert(1)</script>');sid=self.v.add_source(self.root);fid=self.v.ingest(p,sid,'managed')
        code,_,_=self.request('/api/files/'+fid+'/content');self.assertEqual(code,400)
    def test_open_scripts_uses_text_editor(self):
        from unittest.mock import patch
        p=self.root/'danger.command';p.write_text('echo should-not-execute');sid=self.v.add_source(self.root);fid=self.v.ingest(p,sid,'managed')
        with patch('server.subprocess.Popen') as popen:
            code,_,_=self.request('/api/open',{'id':fid});self.assertEqual(code,200)
            self.assertEqual(popen.call_args.args[0][:3],['/usr/bin/open','-a','TextEdit'])
        self.assertEqual(p.read_text(),'echo should-not-execute')
    def test_open_and_reveal_use_the_original_not_the_object(self):
        from unittest.mock import patch
        p=self.root/'note.md';p.write_text('# 笔记')
        sid=self.v.add_source(self.root,'managed');fid=self.v.ingest(p,sid,'managed')
        self.assertIn('objects',self.v.file_row(fid)['vault_path'],'托管副本确实存在')
        with patch('server.subprocess.Popen') as popen:
            code,_,body=self.request('/api/open',{'id':fid,'reveal':True})
            self.assertEqual(code,200);self.assertEqual(json.loads(body)['opened'],'origin')
            self.assertEqual(popen.call_args.args[0],['/usr/bin/open','-R',str(p.resolve())])
            code,_,body=self.request('/api/open',{'id':fid})
            self.assertEqual(json.loads(body)['opened'],'origin')
            self.assertEqual(popen.call_args.args[0][-1],str(p.resolve()),'打开的是原件，不是 data/objects 里的对象')

if __name__=='__main__':unittest.main()
