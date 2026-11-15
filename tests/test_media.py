import io,json,os,shutil,sqlite3,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vault import Vault,extract,EXTRACT_VERSION,digest
from media import normalize_date,HELPER
from safe_content import parse_html,sanitize_svg,html_preview
import test_server

class SafeContentTests(unittest.TestCase):
    def test_html_excludes_active_content_and_decodes_entities(self):
        raw='<head><style>STYLETOKEN</style></head><h1>自动化测试</h1><p>服务器<b>租赁</b> &amp; 合同</p><script type="module">SCRIPTTOKEN</script><template>HIDDEN</template><p hidden>SECRET</p>'
        text=parse_html(raw).text();self.assertIn('服务器租赁 & 合同',text)
        for token in ('STYLETOKEN','SCRIPTTOKEN','HIDDEN','SECRET'):self.assertNotIn(token,text)
    def test_reading_preview_has_no_active_or_external_markup(self):
        raw=b'<meta http-equiv="refresh" content="0;url=https://evil.invalid"><form action="https://evil.invalid"><input><button>Go</button></form><img src="https://evil.invalid/a" onerror="alert(1)" alt="example"><iframe src="file:///etc/passwd"></iframe><p onclick="go()">Hello</p><a href="javascript:go()">Link</a>'
        preview=html_preview(raw,'page.html').decode()
        for token in ('https://evil.invalid','onclick','onerror','<iframe','<form','<input','javascript:','http-equiv'):self.assertNotIn(token,preview)
        self.assertIn('Hello',preview);self.assertIn('Link',preview)
    def test_svg_keeps_geometry_and_removes_scripts_external_references(self):
        raw=b'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" onload="alert(1)"><script>POISON</script><image href="https://evil.invalid/a"/><foreignObject><div>HIDDEN</div></foreignObject><rect width="640" height="480" style="fill: #00ff00; background:url(https://evil.invalid/a)"/><text x="1" y="30">Caption</text></svg>'
        preview,meta,text=sanitize_svg(raw);value=preview.decode()
        self.assertEqual(meta,{'width':640,'height':480});self.assertIn('Caption',text);self.assertIn('fill="#00ff00"',value)
        for token in ('script','POISON','onload','evil.invalid','foreignObject'):self.assertNotIn(token,value)
    def test_svg_rejects_entities(self):
        with self.assertRaises(ValueError):sanitize_svg(b'<!DOCTYPE svg [<!ENTITY x "x">]><svg/>')
    def test_capture_date_validation(self):
        self.assertEqual(normalize_date('2024:06:18 15:30:00+08:00'),'2024-06-18T15:30:00+08:00')
        self.assertEqual(normalize_date('2024:06:18 15:30:00'),'2024-06-18T15:30:00')
        self.assertIsNone(normalize_date('0000:00:00 00:00:00'));self.assertIsNone(normalize_date('not-a-date'))

class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform!='darwin' or not HELPER.exists():raise unittest.SkipTest('需要 macOS 并先构建 bin/MediaProbe')
        cls.fixture_tmp=tempfile.TemporaryDirectory();cls.fixtures=Path(cls.fixture_tmp.name)
        root=Path(__file__).resolve().parents[1];executable=cls.fixtures/'MakeFixtures'
        subprocess.run(['swiftc','-O','-parse-as-library','-module-cache-path',str(root/'.build-cache'),str(root/'tests/MakeMediaFixtures.swift'),'-framework','AVFoundation','-framework','ImageIO','-framework','UniformTypeIdentifiers','-o',str(executable)],check=True,capture_output=True)
        subprocess.run([str(executable),str(cls.fixtures/'media')],check=True,capture_output=True,timeout=45)
    @classmethod
    def tearDownClass(cls):cls.fixture_tmp.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.inputs=self.base/'inputs';shutil.copytree(self.fixtures/'media',self.inputs)
        self.v=Vault(self.base/'data',start_worker=False);self.sid=self.v.add_source(self.inputs,'in-place')
    def tearDown(self):self.v.close();self.temp.cleanup()
    def add(self,name):return self.v.ingest(self.inputs/name,self.sid,'in-place')
    def test_heic_tiff_exif_orientation_and_cached_preview(self):
        for name in ('camera.heic','camera.tiff','camera.jpg'):
            fid=self.add(name);f=self.v.detail(fid)
            self.assertEqual((f['width'],f['height']),(480,640));self.assertEqual(f['camera'],'QA Phone');self.assertEqual(f['captured_at'],'2024-06-18T15:30:00+08:00');self.assertAlmostEqual(f['gps_lat'],31.23)
            self.assertTrue(f['has_thumbnail']);self.assertEqual(f['media_error'],'');thumb=self.v.thumbnail(fid);self.assertTrue(thumb.read_bytes().startswith(b'\xff\xd8'))
            (self.inputs/name).unlink();self.assertFalse(self.v.detail(fid)['available']);self.assertTrue(self.v.detail(fid)['has_thumbnail']);self.assertTrue(self.v.thumbnail(fid).exists())
    def test_valid_video_metadata_poster_and_absent_device(self):
        mov=self.v.detail(self.add('real.mov'));self.assertAlmostEqual(mov['duration'],2,places=1);self.assertEqual((mov['width'],mov['height']),(320,240));self.assertEqual(mov['camera'],'QA Video Phone');self.assertEqual(mov['captured_at'],'2023-08-09T10:11:12+08:00');self.assertTrue(mov['has_thumbnail'])
        mp4=self.v.detail(self.add('real.mp4'));self.assertEqual(mp4['duration'],2);self.assertTrue(mp4['has_thumbnail']);self.assertIsNone(mp4['camera']);self.assertEqual(mp4['captured_source'],'容器创建时间')
    def test_invalid_video_has_explicit_error_without_fabricated_metadata(self):
        (self.inputs/'invalid.mp4').write_text('not a video');f=self.v.detail(self.add('invalid.mp4'));self.assertTrue(f['media_error']);self.assertIsNone(f['duration']);self.assertIsNone(f['captured_at']);self.assertFalse(f['has_thumbnail'])
    def test_managed_video_opens_without_extension_or_external_original(self):
        sid=self.v.add_source(self.inputs,'managed')
        for name in ('real.mp4','real.mov'):
            path=self.inputs/name;before=digest(path);fid=self.v.ingest(path,sid,'managed')
            path.unlink();f=self.v.detail(fid)
            self.assertEqual(f['sha'],before);self.assertEqual(f['media_error'],'')
            self.assertAlmostEqual(f['duration'],2,places=1);self.assertTrue(f['has_thumbnail'])
            self.assertEqual(digest(self.v._resolve(self.v.file_row(fid))),before)
        self.assertFalse(list((self.v.root/'tmp').glob('media-input-*')))
    def test_timeline_and_camera_filters(self):
        self.add('camera.heic');self.add('real.mov');self.add('real.mp4');(self.inputs/'undated.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>');self.add('undated.svg')
        files=self.v.list_files(sort='captured')['files'];self.assertEqual(files[0]['name'],'camera.heic');self.assertEqual(files[-1]['name'],'undated.svg')
        self.assertEqual(self.v.list_files(camera='QA Phone')['total'],1);self.assertEqual(self.v.list_files(month='2023-08')['total'],2);self.assertEqual(self.v.list_files(month='__unknown__')['total'],1)
        self.assertIn('QA Phone',self.v.status()['cameras'])
    def test_pack_roundtrip_rebuilds_media_metadata(self):
        fid=self.add('camera.heic');self.v.edit(fid,tags=['旅行'],starred=True)
        pack=self.v.job_export('test',{})['path'];restored=Vault(self.base/'restored',start_worker=False)
        try:
            restored.job_mount('test',{'path':pack});f=restored.list_files()['files'][0];self.assertTrue(f['has_thumbnail']);self.assertEqual(f['camera'],'QA Phone');self.assertEqual(f['tags'],['旅行'])
        finally:restored.close()
    def test_existing_media_reuses_derivative(self):
        self.add('camera.heic')
        with patch('vault.build_media',side_effect=AssertionError('should be cached')):self.add('camera.heic')

class MigrationTests(unittest.TestCase):
    def test_old_library_is_backed_up_and_html_svg_are_reindexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inputs=root/'source';inputs.mkdir();html=inputs/'old.html';html.write_text('<h1>真正正文</h1><script>STALEJSTOKEN</script>')
            svg=inputs/'old.svg';svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>矢量文字</text></svg>')
            v=Vault(root/'data',start_worker=False);sid=v.add_source(inputs);fid=v.ingest(html,sid,'managed');svgid=v.ingest(svg,sid,'managed');v.edit(fid,tags=['保留标签'],starred=True)
            with v.db() as c:
                c.execute('UPDATE objects SET chunks_json=? WHERE sha=(SELECT sha FROM files WHERE id=?)',(json.dumps([{'text':'STALEJSTOKEN','label':'正文','page':None,'offset':0}]),fid));v._index(c,fid)
                c.execute("UPDATE files SET kind='document' WHERE id=?",(svgid,));c.execute('DROP TABLE media');c.execute('ALTER TABLE objects DROP COLUMN extract_version')
            v.close();v=Vault(root/'data',start_worker=False)
            try:
                backup=root/'data/backups/pre-0.2-metadata.sqlite3';self.assertTrue(backup.exists())
                self.assertEqual(v.status()['jobs'][0]['kind'],'refresh')
                v.job_refresh('test',{});self.assertEqual(v.list_files(q='STALEJSTOKEN')['total'],0);self.assertEqual(v.list_files(q='真正正文')['total'],1)
                self.assertEqual(v.detail(fid)['tags'],['保留标签']);self.assertTrue(v.detail(fid)['starred']);self.assertEqual(v.detail(svgid)['kind'],'image');self.assertTrue(v.detail(svgid)['has_thumbnail'])
            finally:v.close()

class PreviewHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request
    def test_html_reading_is_sandboxed_and_open_uses_preview(self):
        p=self.root/'page.html';p.write_text('<h1>页面正文</h1><script>BADTOKEN</script><img src="https://evil.invalid/a">');sid=self.v.add_source(self.root);fid=self.v.ingest(p,sid,'in-place')
        code,headers,body=self.request('/api/files/'+fid+'/document');self.assertEqual(code,200);self.assertIn('sandbox',headers);self.assertIn('页面正文',body.decode());self.assertNotIn('BADTOKEN',body.decode());self.assertNotIn('evil.invalid',body.decode())
        with patch('server.subprocess.Popen') as popen:
            code,_,body=self.request('/api/open',{'id':fid});self.assertEqual(code,200);self.assertTrue(json.loads(body)['preview_url'].endswith('/document'));self.assertNotIn('token=',json.loads(body)['preview_url']);popen.assert_not_called()
    def test_in_place_text_opens_the_original(self):
        # The user asked to open their own file, so edits must land there; only a
        # missing original falls back to a staged copy.
        p=self.root/'note.md';p.write_text('keep me');sid=self.v.add_source(self.root);fid=self.v.ingest(p,sid,'in-place')
        with patch('server.subprocess.Popen') as popen:
            code,_,body=self.request('/api/open',{'id':fid});self.assertEqual(code,200)
            self.assertEqual(json.loads(body)['opened'],'origin')
            self.assertEqual(Path(popen.call_args.args[0][-1]),p.resolve())
    def test_missing_original_opens_a_staged_copy(self):
        p=self.root/'gone.md';p.write_text('keep me');sid=self.v.add_source(self.root,'managed');fid=self.v.ingest(p,sid,'managed')
        p.unlink()
        with patch('server.subprocess.Popen') as popen:
            code,_,body=self.request('/api/open',{'id':fid});self.assertEqual(code,200)
            self.assertEqual(json.loads(body)['opened'],'copy')
            opened=Path(popen.call_args.args[0][-1])
            self.assertIn('tmp/open',str(opened))
            opened.write_text('edited')
        stored=self.v.root/self.v.file_row(fid)['vault_path']
        self.assertEqual(stored.read_text(),'keep me','编辑器不能改写内容寻址对象')
    def test_svg_preview_classification_and_content(self):
        p=self.root/'icon.svg';p.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>BAD</script><rect width="10" height="10"/></svg>');sid=self.v.add_source(self.root);fid=self.v.ingest(p,sid,'managed')
        self.assertEqual(self.v.detail(fid)['kind'],'image');code,headers,body=self.request('/api/files/'+fid+'/content');self.assertEqual(code,200);self.assertIn('image/svg+xml',headers);self.assertNotIn(b'<script',body);self.assertIn('sandbox',headers)
    def test_video_range_head_and_invalid_ranges(self):
        from server import Handler
        from test_server import FakeSocket
        p=self.root/'video.mp4';p.write_bytes(bytes(range(256))*100);sid=self.v.add_source(self.root);fid=self.v.ingest(p,sid,'in-place')
        def request(value=None,method='GET'):
            headers=[f'{method} /api/files/{fid}/stream HTTP/1.1','Host: 127.0.0.1:12345','X-Vault-Token: test-token','Connection: close']
            if value is not None:headers.append('Range: '+value)
            sock=FakeSocket(('\r\n'.join(headers)+'\r\n\r\n').encode());Handler(sock,('127.0.0.1',99),self.server);head,body=sock.outgoing.getvalue().split(b'\r\n\r\n',1);return int(head.split()[1]),head,body
        code,head,body=request('bytes=100-199');self.assertEqual(code,206);self.assertEqual(body,p.read_bytes()[100:200]);self.assertIn(b'bytes 100-199/25600',head)
        code,_,body=request('bytes=-10');self.assertEqual(code,206);self.assertEqual(body,p.read_bytes()[-10:])
        code,head,body=request('bytes=0-9','HEAD');self.assertEqual(code,206);self.assertEqual(body,b'');self.assertIn(b'Content-Length: 10',head)
        for value in ['bytes=30000-','bytes=-0','bytes=10-1','bytes=0-1,3-4','oops']:self.assertEqual(request(value)[0],416)
        p.unlink();self.assertEqual(request()[0],400)

if __name__=='__main__':unittest.main()
