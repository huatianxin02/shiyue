"""Regression checks for public-release trust boundaries and bounded parsing."""
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from server import Handler
from vault import Vault, digest
from document_extract import extract


class Socket:
    def __init__(self, data):
        self.input = io.BytesIO(data)
        self.output = io.BytesIO()
    def makefile(self, *_): return self.input
    def sendall(self, data): self.output.write(data)


class ReleaseSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.v = Vault(self.root / 'data', start_worker=False)
        self.server = SimpleNamespace(server_port=12345, origin='http://127.0.0.1:12345',
            token='test-owner-token', media_token='test-readonly-media', vault=self.v)
    def tearDown(self):
        self.v.close()
        self.temp.cleanup()
    def request(self, path, method='GET', headers=None, payload=b''):
        pairs = {'Host':'127.0.0.1:12345', 'Connection':'close', **(headers or {})}
        raw = '\r\n'.join([f'{method} {path} HTTP/1.1'] + [f'{k}: {v}' for k,v in pairs.items()])
        sock = Socket(raw.encode() + b'\r\n\r\n' + payload)
        Handler(sock, ('127.0.0.1', 1), self.server)
        head, body = sock.output.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), head.decode(), body
    def test_url_credentials_never_authorize_reads_or_writes(self):
        for method, path in [('GET','/api/status'), ('POST','/api/verify'),
                             ('GET','/api/files/'+'a'*32+'/document')]:
            self.assertEqual(self.request(path+'?token=test-owner-token', method)[0], 403)
    def test_bootstrap_cookie_is_separate_and_scoped(self):
        code, headers, body = self.request('/')
        self.assertEqual(code, 200)
        cookie = next(h for h in headers.splitlines() if h.startswith('Set-Cookie:'))
        self.assertNotIn(self.server.token, cookie)
        for flag in ['HttpOnly', 'SameSite=Strict', 'Path=/api/files/']:
            self.assertIn(flag, cookie)
        self.assertIn(b'test-owner-token', body)
        self.assertIn("frame-ancestors 'none'", headers)
    def test_media_cookie_cannot_read_api_or_mutate_library(self):
        headers = {'Cookie':'shiyue_media=test-readonly-media'}
        for method,path in [('GET','/api/status'),('GET','/api/knowledge/info'),('POST','/api/verify')]:
            self.assertEqual(self.request(path,method,headers)[0],403)
    def test_media_cookie_reads_only_inert_document(self):
        path=self.root/'sample.html';path.write_text('<h1>本地文档</h1><script>bad()</script>')
        fid=self.v.ingest(path,self.v.add_source(self.root),'managed')
        route=f'/api/files/{fid}/document'
        code,headers,body=self.request(route,headers={'Cookie':'shiyue_media=test-readonly-media'})
        self.assertEqual(code,200)
        self.assertNotIn(b'<script>',body)
        self.assertIn('sandbox',headers)
        self.assertEqual(self.request(route,headers={'Cookie':'shiyue_media=wrong'})[0],403)
        self.assertEqual(self.request(route,headers={'Cookie':'shiyue_media=test-readonly-media','Sec-Fetch-Site':'cross-site'})[0],403)
    def test_cross_site_bootstrap_rejected_even_without_origin(self):
        self.assertEqual(self.request('/',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
    def test_chunked_and_truncated_json_rejected(self):
        headers={'X-Vault-Token':self.server.token,'Content-Type':'application/json'}
        self.assertEqual(self.request('/api/verify','POST',{**headers,'Transfer-Encoding':'chunked'})[0],400)
        self.assertEqual(self.request('/api/verify','POST',{**headers,'Content-Length':'10'},b'{}')[0],400)
    def test_agent_proxy_requires_object_before_forwarding(self):
        headers={'X-Vault-Token':self.server.token,'Content-Type':'application/json','Content-Length':'2'}
        with patch('server.agent_catalog.call') as call:
            self.assertEqual(self.request('/api/agent/publish','POST',headers,b'[]')[0],400)
            call.assert_not_called()
    def test_timeout_keeps_original_and_registers_filename(self):
        path=self.root/'malformed.pdf';path.write_bytes(b'%PDF-1.7\ninvalid')
        original=digest(path)
        with patch('document_extract.EXTRACTION_TIMEOUT',0):
            fid=self.v.ingest(path,self.v.add_source(self.root),'managed')
        detail=self.v.detail(fid)
        self.assertIn('时间限制',detail['warning'])
        self.assertEqual(digest(path),original)
        self.assertEqual(digest(self.v._resolve(self.v.file_row(fid))),original)
        self.assertEqual(self.v.list_files(q='malformed')['total'],1)
    def test_parser_cancellation_exits_child(self):
        path=self.root/'cancel.html';path.write_text('<p>safe</p>')
        stop=threading.Event();stop.set()
        with self.assertRaises(InterruptedError):extract(path,path.name,cancel=stop)
    def test_office_entities_rejected_in_utf16(self):
        import zipfile
        path=self.root/'entities.docx'
        xml='<!DOCTYPE doc [<!ENTITY x "unexpected">]><doc><p><t>&x;</t></p></doc>'
        with zipfile.ZipFile(path,'w') as z:z.writestr('word/document.xml',xml.encode('utf-16'))
        chunks,warning=extract(path,path.name)
        self.assertFalse(chunks)
        self.assertIn('实体',warning)
    def test_pdf_worker_uses_pinned_component(self):
        from pypdf import PdfWriter
        path=self.root/'blank.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        with path.open('wb') as output:writer.write(output)
        chunks,warning=extract(path,path.name)
        self.assertFalse(chunks)
        self.assertIn('OCR',warning)


if __name__ == '__main__': unittest.main()
