"""Collections are an index-level grouping: they must never touch the originals."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vault import Vault

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.v = Vault(self.base / 'data', start_worker=False)

    def tearDown(self):
        self.v.close(); self.temp.cleanup()

    def write(self, folder, name, text='内容'):
        folder = self.base / folder; folder.mkdir(parents=True, exist_ok=True)
        path = folder / name; path.write_text(text, encoding='utf-8'); return path

    def load(self, folder, names, mode='managed'):
        sid = self.v.add_source(self.base / folder, mode)
        for name in names: self.v.ingest(self.base / folder / name, sid, mode)
        return sid

    def by_name(self):
        return {f['name']: f['id'] for f in self.v.list_files()['files']}

    def execute(self, kind, payload):
        job = self.v.queue(kind, payload)
        with self.v.db() as c: row = dict(c.execute('SELECT * FROM jobs WHERE id=?', (job['job_id'],)).fetchone())
        self.v.run_job(row)
        return next(j for j in self.v.status()['jobs'] if j['id'] == job['job_id'])

    def test_new_collection_is_empty_and_trimmed(self):
        created = self.v.create_collection('  AI  资料 ')
        self.assertEqual(created['name'], 'AI 资料', '多余空格要合并，否则侧栏会出现两个看起来一样的分类')
        self.assertEqual(created['count'], 0)
        self.assertEqual([c['name'] for c in self.v.collections()], ['AI 资料'])

    def test_duplicate_and_invalid_names_are_rejected(self):
        self.v.create_collection('AI资料')
        with self.assertRaises(ValueError): self.v.create_collection('AI资料')
        self.v.create_collection('AI')
        with self.assertRaises(ValueError): self.v.create_collection('ai')
        for bad in ('', '   ', 'x' * 41):
            with self.assertRaises(ValueError): self.v.create_collection(bad)

    def test_assign_and_filter_without_moving_files(self):
        self.write('docs', 'paper.md', '注意力机制 综述')
        self.write('docs', 'budget.md', '预算审批 流程')
        self.load('docs', ['paper.md', 'budget.md'])
        ids = self.by_name()
        original = (self.base / 'docs' / 'paper.md').read_bytes()
        c = self.v.create_collection('AI资料')

        result = self.v.assign_collection(c['id'], [ids['paper.md']])
        self.assertEqual((result['changed'], result['collection']['count']), (1, 1))
        self.assertEqual([f['name'] for f in self.v.list_files(collection=c['id'])['files']], ['paper.md'])
        self.assertEqual(self.v.list_files()['total'], 2, '归类不能把文件从全部资料里拿走')
        self.assertEqual((self.base / 'docs' / 'paper.md').read_bytes(), original, '原件必须逐字节不变')

    def test_filter_combines_with_search(self):
        self.write('docs', 'paper.md', '注意力机制 综述')
        self.write('docs', 'budget.md', '预算审批 流程')
        self.load('docs', ['paper.md', 'budget.md'])
        ids = self.by_name(); c = self.v.create_collection('AI资料')
        self.v.assign_collection(c['id'], [ids['paper.md']])
        self.assertEqual(self.v.list_files(q='注意力', collection=c['id'])['total'], 1)
        self.assertEqual(self.v.list_files(q='预算', collection=c['id'])['total'], 0, '分类内搜索不能漏到分类外')

    def test_assign_is_idempotent(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']; c = self.v.create_collection('待读')
        self.v.assign_collection(c['id'], [fid])
        again = self.v.assign_collection(c['id'], [fid])
        self.assertEqual((again['changed'], again['collection']['count']), (0, 1))
        self.assertEqual([(row['id'],row['name']) for row in self.v.file_collections(fid)], [(c['id'],'待读')])

    def test_assign_with_name_reuses_an_existing_collection(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']; c = self.v.create_collection('AI资料')
        result = self.v.assign_collection(None, [fid], name='AI资料')
        self.assertEqual((result['collection']['id'], result['created']), (c['id'], False))
        self.assertEqual(len(self.v.collections()), 1, '同名不应产生第二个分类')
        fresh = self.v.assign_collection(None, [fid], name='待读')
        self.assertTrue(fresh['created'])

    def test_remove_from_collection_keeps_the_file(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']; c = self.v.create_collection('待读')
        self.v.assign_collection(c['id'], [fid])
        result = self.v.assign_collection(c['id'], [fid], action='remove')
        self.assertEqual((result['changed'], result['collection']['count']), (1, 0))
        self.assertEqual(self.v.list_files()['total'], 1)
        self.assertEqual(self.v.detail(fid)['collections'], [])

    def test_deleting_a_collection_keeps_files(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']; c = self.v.create_collection('待读')
        self.v.assign_collection(c['id'], [fid])
        removed = self.v.delete_collection(c['id'])
        self.assertEqual((removed['name'], removed['files']), ('待读', 1))
        self.assertEqual(self.v.collections(), [])
        self.assertEqual(self.v.list_files()['total'], 1, '删分类不等于删资料')
        self.assertEqual(self.v.detail(fid)['collections'], [])

    def test_removing_a_file_drops_its_membership(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']; c = self.v.create_collection('待读')
        self.v.assign_collection(c['id'], [fid])
        self.v.permanently_remove_files([fid])
        self.assertEqual(self.v.collections()[0]['count'], 0)
        self.assertEqual(self.v.collections()[0]['name'], '待读', '分类本身要留着')

    def test_disconnecting_a_source_drops_memberships(self):
        self.write('docs', 'a.md'); sid = self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']; c = self.v.create_collection('待读')
        self.v.assign_collection(c['id'], [fid])
        self.v.permanently_remove_source(sid)
        self.assertEqual(self.v.collections()[0]['count'], 0)
        self.assertEqual(self.v.status()['stats']['files'], 0)

    def test_detail_lists_every_collection_of_a_file(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        fid = self.by_name()['a.md']
        for name in ('AI资料', '待读'):
            self.v.assign_collection(None, [fid], name=name)
        self.assertEqual([c['name'] for c in self.v.detail(fid)['collections']], ['AI资料', '待读'])

    def test_rename_rejects_a_taken_name(self):
        a = self.v.create_collection('AI资料'); self.v.create_collection('待读')
        with self.assertRaises(ValueError): self.v.rename_collection(a['id'], '待读')
        self.assertEqual(self.v.rename_collection(a['id'], '机器学习')['name'], '机器学习')
        self.assertEqual(self.v.rename_collection(a['id'], '机器学习')['name'], '机器学习', '改成自己现在的名字应当允许')

    def test_unknown_collection_is_an_error(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        with self.assertRaises(ValueError):
            self.v.assign_collection('missing', [self.by_name()['a.md']])
        with self.assertRaises(ValueError):
            self.v.delete_collection('missing')

    def test_assign_requires_a_selection(self):
        c = self.v.create_collection('待读')
        with self.assertRaises(ValueError): self.v.assign_collection(c['id'], [])

    def test_manifest_writes_a_readable_shelf_list(self):
        self.write('docs', 'a.md'); self.load('docs', ['a.md'])
        self.v.assign_collection(None, [self.by_name()['a.md']], name='AI资料')
        rows = (self.v.root / 'collections.csv').read_text(encoding='utf-8-sig').strip().splitlines()
        self.assertEqual(rows[0], 'collection,file_id,filename,sha256')
        self.assertTrue(rows[1].startswith('AI资料,'))
        self.assertIn('a.md', rows[1])
        self.assertTrue((self.v.root / 'manifest.csv').exists(), '原清单格式保持不变')

    def test_pack_carries_collections_and_mount_restores_them(self):
        self.write('docs', 'a.md', '注意力机制'); self.write('docs', 'b.md', '预算审批')
        self.load('docs', ['a.md', 'b.md'])
        ids = self.by_name()
        self.v.assign_collection(None, [ids['a.md']], name='AI资料')

        exported = self.execute('export', {'ids': []})
        self.assertEqual(exported['state'], 'done')
        mounted = self.execute('mount', {'path': exported['result']['path']})
        self.assertEqual(mounted['state'], 'done')

        shelves = self.v.collections()
        self.assertEqual([s['name'] for s in shelves], ['AI资料'])
        self.assertEqual(shelves[0]['count'], 2, '挂载的文件应并入同名分类')
        modes = sorted(f['mode'] for f in self.v.list_files(collection=shelves[0]['id'])['files'])
        self.assertEqual(modes, ['managed', 'read-only'])

if __name__ == '__main__':
    unittest.main()
