"""A real loopback server and worker exercise the user's main workflow end to end."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Server
from vault import Vault


class WorkflowTests(unittest.TestCase):
    def test_import_search_organize_export_verify_and_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root / 'inputs'; inputs.mkdir()
            (inputs / '项目.md').write_text('青山项目的离线资料检索与归档。', encoding='utf-8')
            (inputs / '记录.csv').write_text('项目,进度\n青山,完成\n', encoding='utf-8')
            (inputs / '页面.html').write_text('<h1>青山网页</h1><script>POISON</script><table><tr><td>离线阅读</td></tr></table>', encoding='utf-8')
            (inputs / '示意.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="40" height="30" fill="green"/></svg>')
            vault = Vault(root / 'vault')
            try: server = Server(('127.0.0.1', 0), vault)
            except Exception:
                vault.close(); raise
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            def request(route, body=None, authenticated=True):
                conn = http.client.HTTPConnection(*server.server_address, timeout=10)
                headers = {'Content-Type': 'application/json'}
                if authenticated: headers['X-Vault-Token'] = server.token
                try:
                    conn.request('GET' if body is None else 'POST', route,
                                 None if body is None else json.dumps(body), headers)
                    response = conn.getresponse(); data = response.read()
                    return response.status, data
                finally: conn.close()
            def api(route, body=None):
                status, data = request('/api' + route, body)
                self.assertEqual(status, 200, data.decode())
                return json.loads(data)
            def wait_job(job):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    row = next(j for j in api('/status')['jobs'] if j['id'] == job['job_id'])
                    if row['state'] not in ('queued', 'running'):
                        self.assertEqual(row['state'], 'done', row)
                        return row['result']
                    time.sleep(.03)
                self.fail('background job did not finish')
            try:
                self.assertEqual(request('/api/status', authenticated=False)[0], 403)
                self.assertEqual(request('/')[0], 200)
                self.assertIn(b'filter-toggle', request('/')[1])
                wait_job(api('/import', {'paths': [str(inputs)], 'mode': 'managed'}))
                self.assertEqual(api('/status')['stats']['files'], 4)
                hits = api('/files?q=%E9%9D%92%E5%B1%B1')['files']
                self.assertEqual(len(hits), 3)
                fid = next(f['id'] for f in hits if f['name'].endswith('.md'))
                api('/edit', {'id': fid, 'tags': ['回归验证'], 'starred': True})
                self.assertEqual(api('/files?starred=1')['total'], 1)
                self.assertEqual(api('/files?q=%E5%9B%9E%E5%BD%92%E9%AA%8C%E8%AF%81')['total'], 1)
                shelf = api('/collection/create', {'name': 'AI资料'})
                api('/collection/create', {'name': '待读'})
                api('/collection/assign', {'collection_id': shelf['id'], 'ids': [fid]})
                self.assertEqual(api('/files?collection=' + shelf['id'])['total'], 1)
                html_id = next(f['id'] for f in hits if f['name'].endswith('.html'))
                status, preview = request('/api/files/' + html_id + '/document')
                self.assertEqual(status, 200); self.assertNotIn(b'POISON', preview)
                self.assertIn('离线阅读'.encode(), preview)
                exported = wait_job(api('/export', {}))
                verified = wait_job(api('/verify', {}))
                self.assertEqual(verified['verified'], 4)
                restored = Vault(root / 'restored', start_worker=False)
                try:
                    mounted = restored.job_mount('test', {'path': exported['path']})
                    self.assertTrue(mounted['verified'])
                    self.assertEqual(restored.status()['stats']['files'], 4)
                    found = restored.list_files(q='回归验证')['files'][0]
                    detail = restored.detail(found['id'])
                    self.assertTrue(detail['starred'])
                    self.assertEqual([c['name'] for c in detail['collections']], ['AI资料'])
                finally: restored.close()
            finally:
                server.shutdown(); thread.join(5); server.server_close(); vault.close()
