"""Exercise the Vault Agent page's real HTTP entry points in a temporary library."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent_catalog
from server import Handler
from vault import Vault
from test_server import FakeSocket


class AgentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = Vault(self.root/'vault',start_worker=False)
        self.addCleanup(self.vault.close)
        self.server = SimpleNamespace(server_port=12345,origin='http://127.0.0.1:12345',token='vault-private-token',media_token='test-media-cookie',vault=self.vault)
        self.console = self.root/'console'
        self.console.mkdir()
        (self.console/'runtime.json').write_text(json.dumps({'origin':'http://127.0.0.1:12500','token':'console-private-token'}))
        flags = patch.dict(os.environ,{'SHIYUE_CONSOLE_DATA':str(self.console)})
        flags.start()
        self.addCleanup(flags.stop)

    def request(self,path,body=None,token='vault-private-token'):
        payload = json.dumps(body).encode() if body is not None else b''
        method = 'GET' if body is None else 'POST'
        headers = [method+' '+path+' HTTP/1.1','Host: 127.0.0.1:12345','Connection: close']
        if token is not None:headers.append('X-Vault-Token: '+token)
        if body is not None:headers.extend(['Content-Type: application/json','Content-Length: '+str(len(payload))])
        socket = FakeSocket(('\r\n'.join(headers)+'\r\n\r\n').encode()+payload)
        Handler(socket,('127.0.0.1',999),self.server)
        header,result = socket.outgoing.getvalue().split(b'\r\n\r\n',1)
        return int(header.split()[1]),header.decode(),result

    def proxy(self,path,payload=None):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"ok":true}')
        with patch.object(agent_catalog.urllib.request,'build_opener',return_value=opener):
            status,headers,body = self.request(path,payload)
        return status,headers,body,opener

    def test_final_version_and_reference_routes_preserve_payload_and_token_boundary(self):
        for route,payload in [('artifact-final',{'id':'F-test','sha256':'a'*64,'confirmed':True}),
                              ('references/resolve',{'references':[{'id':'b'*32,'sha256':'a'*64}]})]:
            code,headers,body,opener = self.proxy('/api/agent/'+route,payload)
            self.assertEqual(code,200)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url,'http://127.0.0.1:12500/api/bridge/'+route)
            self.assertEqual(json.loads(request.data),payload)
            self.assertEqual(request.get_header('X-console-token'),'console-private-token')
            self.assertNotIn(b'private-token',body)
        code,_,_,opener = self.proxy('/api/agent/artifact-versions?id=F-test')
        self.assertEqual(code,200)
        self.assertTrue(opener.open.call_args.args[0].full_url.endswith('/artifact-versions?id=F-test'))

    def test_open_console_is_fixed_local_post_and_needs_no_console_descriptor(self):
        (self.console/'runtime.json').unlink()
        with patch.object(agent_catalog.subprocess,'run') as launch, patch.object(agent_catalog.urllib.request,'build_opener') as opener:
            code,_,body = self.request('/api/agent/open-console',{'path':'/tmp/untrusted.app','command':'whoami'})
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body),{'ok':True,'opened':True})
        launch.assert_called_once_with(['/usr/bin/open',str(Path.home()/'Desktop'/'小鲸控制台.app')],
            check=True,timeout=8,stdout=agent_catalog.subprocess.DEVNULL,stderr=agent_catalog.subprocess.DEVNULL)
        opener.assert_not_called()

    def test_open_console_rejects_get_and_unauthenticated_requests(self):
        with patch.object(agent_catalog.subprocess,'run') as launch:
            self.assertEqual(self.request('/api/agent/open-console')[0],400)
            self.assertEqual(self.request('/api/agent/open-console',{},token=None)[0],403)
            self.assertEqual(self.request('/api/agent/open-console',{},token='wrong')[0],403)
        launch.assert_not_called()

    def test_open_console_timeout_is_bounded_and_does_not_expose_command(self):
        with patch.object(agent_catalog.subprocess,'run',side_effect=agent_catalog.subprocess.TimeoutExpired('private-command',8)):
            code,_,body = self.request('/api/agent/open-console',{})
        self.assertEqual(code,400)
        self.assertIn('小鲸控制台未能打开',json.loads(body)['error'])
        self.assertNotIn(b'private-command',body)

    def test_qq_confirm_and_backup_writes_are_not_proxied(self):
        for route in ['confirm','backup/create','joint-backup/create','arbitrary-command']:
            code,_,_,opener = self.proxy('/api/agent/'+route,{})
            self.assertEqual(code,400)
            opener.open.assert_not_called()

    def test_agent_proxy_requires_vault_auth_and_rejects_remote_descriptors(self):
        code,_,_ = self.request('/api/agent/artifact-versions?id=F-test',token=None)
        self.assertEqual(code,403)
        (self.console/'runtime.json').write_text(json.dumps({'origin':'https://outside.invalid','token':'private'}))
        code,_,body,opener = self.proxy('/api/agent/info')
        self.assertEqual(code,400)
        opener.open.assert_not_called()
        self.assertNotIn(b'outside.invalid',body)

    def test_agent_page_uses_vault_identity_and_isolated_preview_policy(self):
        code,headers,body = self.request('/integration.html',token=None)
        self.assertEqual(code,200)
        self.assertIn(b'content="vault"',body)
        self.assertIn(b'vault-private-token',body)
        self.assertNotIn(b'__VAULT_TOKEN__',body)
        self.assertIn("img-src 'self' data: blob:",headers)
        self.assertIn("frame-src 'self' blob:",headers)
        self.assertIn("style-src 'self' 'unsafe-inline'",headers)
        _,main_headers,_ = self.request('/',token=None)
        self.assertNotIn('blob:',main_headers)

    def test_agent_assets_load_via_static_routes(self):
        for route in ['/integration.js','/integration.css','/artifact-preview.js','/joint-backup.js']:
            code,headers,body = self.request(route,token=None)
            self.assertEqual(code,200,route)
            self.assertTrue(body)
            self.assertIn('text/javascript' if route.endswith('.js') else 'text/css',headers)


if __name__=='__main__':
    unittest.main()
