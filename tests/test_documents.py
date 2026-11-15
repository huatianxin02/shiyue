import json, subprocess, tempfile, unittest
from pathlib import Path
from vault import Vault

class DocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.base=Path(cls.temp.name);root=Path(__file__).resolve().parents[1]
        executable=cls.base/'MakeDocs'
        subprocess.run(['swiftc','-O','-parse-as-library','-module-cache-path',str(root/'.build-cache'),str(root/'tests/MakeDocumentFixtures.swift'),'-framework','Cocoa','-framework','PDFKit','-framework','ImageIO','-framework','UniformTypeIdentifiers','-o',str(executable)],check=True,capture_output=True)
        subprocess.run([str(executable),str(cls.base/'inputs')],check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_real_scanned_pdf_page_render_ocr_search_and_position(self):
        with tempfile.TemporaryDirectory() as temp:
            v=Vault(Path(temp)/'vault',start_worker=False)
            try:
                sid=v.add_source(self.base/'inputs','managed');fid=v.ingest(self.base/'inputs/scan.pdf',sid,'managed')
                self.assertEqual(v.document_probe(fid)['pages'],2)
                page=v.document_probe(fid,'page',2);self.assertTrue(page.read_bytes().startswith(b'\xff\xd8'))
                before=v.file_row(fid)['sha'];result=v.job_ocr('test',{'id':fid})
                self.assertEqual(result['pages'],2);self.assertGreater(result['lines'],2)
                self.assertEqual(v.list_files(q='SHIYUE')['total'],1)
                self.assertEqual(v.file_row(fid)['sha'],before)
                self.assertEqual(v.reading_position(fid,2)['page'],2)
                self.assertTrue(any(ch.get('bounds') and ch['page']==2 for ch in v.detail(fid)['chunks']))
            finally:v.close()
    def test_image_ocr_chinese_and_english(self):
        with tempfile.TemporaryDirectory() as temp:
            v=Vault(Path(temp)/'vault',start_worker=False)
            try:
                sid=v.add_source(self.base/'inputs','managed');fid=v.ingest(self.base/'inputs/ocr.png',sid,'managed')
                v.job_ocr('test',{'id':fid});self.assertEqual(v.list_files(q='SHIYUE')['total'],1)
                self.assertEqual(v.list_files(q='青山')['total'],1)
            finally:v.close()
