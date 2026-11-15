"""Removing individual files, especially ones whose original is already gone.

The reported case: a file was indexed, the original was deleted outside the app,
and the entry could not be removed any more. These tests pin both halves - the
entry does go away, and the app never pretends to delete files it does not own.
"""
import sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vault import Vault


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.v = Vault(self.base / 'vault', start_worker=False)

    def tearDown(self): self.v.close(); self.temp.cleanup()

    def write(self, folder, name, body):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(body, encoding='utf-8')
        return path

    def load(self, folder, names, mode='managed'):
        sid = self.v.add_source(folder, mode)
        for name in names: self.v.ingest(folder / name, sid, mode)
        return sid

    def load_file(self, path, mode='managed'):
        """What "选择文件" does: one file becomes its own source."""
        sid = self.v.add_source(path, mode, 'file')
        self.v.ingest(path, sid, mode)
        return sid

    def one(self, **kw):
        rows = self.v.list_files(**kw)['files']
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def object_path(self, sha): return self.v.root / 'objects' / sha[:2] / sha

    def wait(self, predicate, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate(): return True
            time.sleep(.05)
        return predicate()


class FileRemovalTests(Base):
    def test_entry_goes_away_when_the_original_is_already_deleted(self):
        folder = self.base / 'docs'
        original = self.write(folder, 'gone.md', '# 已经删掉的原件\n正文')
        self.load_file(original, mode='in-place')
        row = self.one()
        original.unlink()  # deleted outside the app, which is how this starts

        impact = self.v.file_impact([row['id']])
        self.assertEqual((impact['files'], impact['offline'], impact['unique']), (1, 1, 0))

        result = self.v.permanently_remove_files([row['id']])

        self.assertEqual(result['files'], 1)
        self.assertEqual(self.v.list_files()['total'], 0)
        self.assertEqual(self.v.list_files(q='正文')['total'], 0, '检索索引也要清掉')
        self.assertEqual(self.v.status()['sources'], [], '单文件来源应随文件一起消失')
        with self.assertRaises(ValueError):
            self.v.detail(row['id'])

    def test_managed_copy_is_flagged_as_the_last_surviving_one(self):
        folder = self.base / 'docs'
        original = self.write(folder, 'keep.md', '只有资料库还留着')
        self.load(folder, ['keep.md'])
        row = self.one()
        self.assertTrue(self.object_path(row['sha']).is_file())
        original.unlink()

        impact = self.v.file_impact([row['id']])
        self.assertEqual((impact['unique'], impact['offline']), (1, 0), '原件没了，托管副本就是唯一一份')
        self.assertGreater(impact['bytes'], 0)

        self.v.permanently_remove_files([row['id']])
        self.assertTrue(self.wait(lambda: not self.object_path(row['sha']).exists()), '副本应被回收')
        self.assertEqual(self.v.list_files()['total'], 0)

    def test_original_is_never_deleted(self):
        folder = self.base / 'docs'
        original = self.write(folder, 'here.md', '原件还在')
        self.load(folder, ['here.md'])
        row = self.one()

        self.assertEqual(self.v.file_impact([row['id']])['unique'], 0, '原件在，不算唯一副本')
        self.v.permanently_remove_files([row['id']])

        self.assertTrue(original.is_file(), '移除记录绝不能删原件')
        self.assertEqual(original.read_text(encoding='utf-8'), '原件还在')

    def test_folder_source_stays_but_single_file_source_does_not(self):
        folder = self.base / 'mixed'
        self.write(folder, 'a.md', 'aaa'); self.write(folder, 'b.md', 'bbb')
        self.load(folder, ['a.md', 'b.md'])
        first = self.v.list_files(sort='name')['files'][0]
        self.v.permanently_remove_files([first['id']])
        sources = self.v.status()['sources']
        self.assertEqual([(s['name'], s['count']) for s in sources], [('mixed', 1)], '文件夹来源要留下')

        lone = self.write(self.base / 'lone', 'only.md', 'only')
        self.load_file(lone)
        self.assertEqual(len(self.v.status()['sources']), 2)
        self.v.permanently_remove_files([self.one(source=self.v.status()['sources'][1]['id'])['id']])
        self.assertEqual([s['name'] for s in self.v.status()['sources']], ['mixed'])

    def test_shared_bytes_survive_until_the_last_reference_goes(self):
        folder = self.base / 'docs'
        self.write(folder, 'a.md', '同样的内容')
        self.write(folder, 'b.md', '同样的内容')
        self.load(folder, ['a.md', 'b.md'])
        rows = self.v.list_files()['files']
        self.assertEqual(len({r['sha'] for r in rows}), 1)
        sha = rows[0]['sha']

        impact = self.v.file_impact([rows[0]['id']])
        self.assertEqual((impact['objects'], impact['shared']), (1, 1))

        self.assertEqual(self.v.permanently_remove_files([rows[0]['id']])['objects'], 0, '还有一份在用，对象不能删')
        self.assertTrue(self.object_path(sha).is_file())
        self.assertEqual(self.v.list_files()['total'], 1)

        self.assertEqual(self.v.permanently_remove_files([rows[1]['id']])['objects'], 1)
        self.assertTrue(self.wait(lambda: not self.object_path(sha).exists()))

    def test_batch_removal_clears_a_selection(self):
        folder = self.base / 'docs'
        names = [f'f{i}.md' for i in range(5)]
        for n in names: self.write(folder, n, n)
        self.load(folder, names)
        ids = [r['id'] for r in self.v.list_files()['files']][:3]

        result = self.v.permanently_remove_files(ids)

        self.assertEqual(result['files'], 3)
        self.assertEqual(self.v.list_files()['total'], 2)
        for fid in ids:
            with self.assertRaises(ValueError): self.v.detail(fid)

    def test_bad_requests_are_rejected(self):
        for payload in ([], [''], [None], 'not-a-list'):
            with self.assertRaises(ValueError): self.v.permanently_remove_files(payload)
        with self.assertRaises(ValueError): self.v.permanently_remove_files([f'x{i}' for i in range(1001)])
        with self.assertRaises(ValueError): self.v.file_impact(['missing-id'])

    def test_manifest_drops_the_removed_entry(self):
        folder = self.base / 'docs'
        self.write(folder, 'stay.md', 'stay'); self.write(folder, 'go.md', 'go')
        self.load(folder, ['stay.md', 'go.md'])
        row = [r for r in self.v.list_files()['files'] if r['name'] == 'go.md'][0]
        self.v.permanently_remove_files([row['id']])
        manifest = (self.v.root / 'manifest.csv').read_text(encoding='utf-8-sig')
        self.assertIn('stay.md', manifest)
        self.assertNotIn('go.md', manifest)


class SourceRemovalStillWorks(Base):
    def test_removing_a_source_reuses_the_same_gc(self):
        folder = self.base / 'all'
        self.write(folder, 'a.md', 'a'); self.write(folder, 'b.md', 'b')
        sid = self.load(folder, ['a.md', 'b.md'])
        shas = [r['sha'] for r in self.v.list_files()['files']]
        result = self.v.permanently_remove_source(sid)
        self.assertEqual((result['files'], result['objects']), (2, 2))
        self.assertTrue(self.wait(lambda: not any(self.object_path(s).exists() for s in shas)))
        self.assertEqual(self.v.list_files()['total'], 0)


if __name__ == '__main__':
    unittest.main()
