"""Real loopback routes: disk inventory remains read-only until selection."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.parse

from server import Server
from vault import Vault


class DiscoveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.inputs=self.root/'input';self.inputs.mkdir()
        (self.inputs/'入选报告.md').write_text('松鹤计划最终完成，本地索引可供后续读取。')
        (self.inputs/'未选资料.txt').write_text('不应自动导入的资料')
        self.v=Vault(self.root/'library')
        self.server=Server(('127.0.0.1',0),self.v)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.thread.join(5);self.server.server_close();self.v.close();self.temp.cleanup()

    def request(self,path,body=None,token=True,origin=None):
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=10)
        headers={'Content-Type':'application/json'}
        if token:headers['X-Vault-Token']=self.server.token
        if origin:headers['Origin']=origin
        try:
            connection.request('GET' if body is None else 'POST',path,
                               body=None if body is None else json.dumps(body),headers=headers)
            response=connection.getresponse();return response.status,json.loads(response.read())
        finally:connection.close()

    def api(self,path,body=None):
        code,data=self.request(path,body);self.assertEqual(code,200,data);return data

    def wait_for(self,path,key,terminal):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            value=self.api(path)
            if value[key] in terminal:return value
            time.sleep(.02)
        self.fail('route did not reach terminal state: '+path)

    def test_authenticated_routes_and_owner_api_schema(self):
        for route in ['/api/discovery/locations','/api/ingest/schema','/api/ingest/config']:
            self.assertEqual(self.request(route,token=False)[0],403)
            self.assertEqual(self.request(route+'?token='+self.server.token,token=False)[0],403)
            self.assertEqual(self.request(route,origin='https://example.test')[0],403)
        self.assertEqual(self.request('/api/discovery/start?token='+self.server.token,
                                     {'paths':[str(self.inputs)]},token=False)[0],403)
        locations=self.api('/api/discovery/locations');self.assertIsInstance(locations['locations'],list)
        schema=self.api('/api/ingest/schema')
        self.assertEqual(schema['submit']['path'],'/api/ingest/files')
        config=self.api('/api/ingest/config')
        self.assertEqual(config['data'],str(self.v.root));self.assertTrue(Path(config['script']).is_file())

    def test_scan_then_select_import_and_read_completed_receipt(self):
        start=self.api('/api/discovery/start',{'paths':[str(self.inputs)]})
        scan=self.wait_for('/api/discovery/scan?id='+start['id'],'state',{'done','failed','limited'})
        self.assertEqual(scan['state'],'done');self.assertEqual(scan['total'],2)
        self.assertEqual(self.api('/api/files')['total'],0)
        with self.v.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sources').fetchone()[0],0)
        filtered=self.api('/api/discovery/scan?'+urllib.parse.urlencode({'id':start['id'],'kind':'document','q':'入选','limit':1}))
        self.assertEqual(filtered['filtered_total'],1)
        item=filtered['items'][0]
        imported=self.api('/api/discovery/import',{'id':start['id'],'ids':[item['id']],'mode':'in-place'})
        complete=self.wait_for('/api/ingest/jobs/'+imported['job_id'],'state',{'done','partial','failed','cancelled'})
        self.assertEqual(complete['state'],'done',complete);self.assertTrue(complete['index_ready'])
        self.assertTrue(complete['files'][0]['text_indexed']);self.assertEqual(complete['requested'],1)
        self.assertEqual(self.api('/api/files')['total'],1)
        hits=self.api('/api/knowledge/search?q='+urllib.parse.quote('松鹤'))['results']
        self.assertEqual(hits[0]['id'],complete['files'][0]['id'])
        again=self.api('/api/discovery/import',{'id':start['id'],'ids':[item['id']],'mode':'in-place'})
        self.assertTrue(again['reused']);self.assertEqual(again['job_id'],imported['job_id'])

    def test_invalid_request_returns_400_and_changed_selection_is_not_queued(self):
        self.assertEqual(self.request('/api/discovery/start',[])[0],400)
        self.assertEqual(self.request('/api/discovery/start',{'paths':[str(self.inputs/'入选报告.md')]} )[0],400)
        start=self.api('/api/discovery/start',{'paths':[str(self.inputs)]})
        scan=self.wait_for('/api/discovery/scan?id='+start['id'],'state',{'done'})
        self.assertEqual(self.request('/api/discovery/scan?id='+start['id']+'&limit=huge')[0],400)
        self.assertEqual(self.request('/api/discovery/import',{'id':start['id'],'ids':['unknown']})[0],400)
        item=scan['items'][0];Path(item['path']).write_text('文件已发生变化，旧扫描结果不可直接提交')
        code,error=self.request('/api/discovery/import',{'id':start['id'],'ids':[item['id']]})
        self.assertEqual(code,400);self.assertIn('变化',error['error'])
        with self.v.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        stopped=self.api('/api/discovery/cancel',{'id':start['id']})
        self.assertEqual(stopped['id'],start['id'])


if __name__=='__main__':unittest.main()
