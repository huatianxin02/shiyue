"""Open and reveal must land on the user's own file, not on the object store."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vault import Vault

class OpenTargetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.v = Vault(self.base / 'data', start_worker=False)

    def tearDown(self):
        self.v.close(); self.temp.cleanup()

    def make(self, name='notes.html', text='<h1>笔记</h1>'):
        folder = self.base / 'notes'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / name; path.write_text(text, encoding='utf-8'); return path

    def test_managed_copy_still_opens_the_original(self):
        path = self.make()
        sid = self.v.add_source(self.base / 'notes', 'managed')
        fid = self.v.ingest(path, sid, 'managed')
        f = self.v.file_row(fid)

        self.assertNotEqual(self.v._resolve(f), path, '预览与导出仍应使用托管对象')
        target, kind = self.v.open_target(f)
        self.assertEqual(kind, 'origin')
        self.assertEqual(target, path.resolve())

    def test_in_place_file_opens_the_original(self):
        path = self.make()
        sid = self.v.add_source(self.base / 'notes', 'in-place')
        fid = self.v.ingest(path, sid, 'in-place')
        target, kind = self.v.open_target(self.v.file_row(fid))
        self.assertEqual((kind, target), ('origin', path.resolve()))

    def test_deleted_original_falls_back_to_the_managed_copy(self):
        path = self.make()
        sid = self.v.add_source(self.base / 'notes', 'managed')
        fid = self.v.ingest(path, sid, 'managed')
        path.unlink()
        target, kind = self.v.open_target(self.v.file_row(fid))
        self.assertEqual(kind, 'copy')
        self.assertEqual(target, self.v.root / self.v.file_row(fid)['vault_path'])

    def test_mounted_pack_uses_its_verified_copy(self):
        path = self.make()
        sid = self.v.add_source(self.base / 'notes', 'read-only', 'pack')
        fid = self.v.ingest(path, sid, 'read-only', origin='pack://abc/' + 'f' * 32)
        target, kind = self.v.open_target(self.v.file_row(fid))
        self.assertEqual(kind, 'copy')
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent.parent.name, 'objects')

    def test_nothing_available_returns_none(self):
        path = self.make()
        sid = self.v.add_source(self.base / 'notes', 'in-place')
        fid = self.v.ingest(path, sid, 'in-place')
        path.unlink()
        self.assertEqual(self.v.open_target(self.v.file_row(fid)), (None, None))

if __name__ == '__main__':
    unittest.main()
