"""Disconnecting a source and deleting background tasks.

The user's complaint was concrete: a folder of miscellaneous files got indexed and
could not be taken back out, and the import task could only be paused. These tests
pin the two promises that make the fix trustworthy - the index really goes away, and
the originals really stay.
"""
import sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vault import Vault, JobCancelled, now


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

    def object_path(self, sha): return self.v.root / 'objects' / sha[:2] / sha

    def wait(self, predicate, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate(): return True
            time.sleep(.05)
        return predicate()


class DisconnectTests(Base):
    def test_index_and_managed_copies_go_away(self):
        folder = self.base / 'misc'
        self.write(folder, 'a.md', '# 会议纪要\n预算审批流程')
        self.write(folder, 'b.md', '# 旅行清单\n护照与充电器')
        sid = self.load(folder, ['a.md', 'b.md'])
        shas = [f['sha'] for f in self.v.list_files()['files']]
        self.assertEqual(len(shas), 2)
        self.assertTrue(all(self.object_path(s).is_file() for s in shas))

        impact = self.v.source_impact(sid)
        self.assertEqual((impact['files'], impact['objects'], impact['removed'], impact['shared']), (2, 2, 2, 0))
        self.assertGreater(impact['bytes'], 0)

        result = self.v.permanently_remove_source(sid)
        self.assertEqual((result['files'], result['objects']), (2, 2))

        status = self.v.status()
        self.assertEqual(status['sources'], [])
        self.assertEqual(status['stats']['files'], 0)
        self.assertEqual(self.v.list_files()['total'], 0)
        self.assertEqual(self.v.list_files(q='预算')['total'], 0, '搜索索引必须一并清除')
        self.assertTrue(self.wait(lambda: not any(self.object_path(s).exists() for s in shas)), '托管副本应在后台删除')
        self.assertFalse((self.v.root / 'objects' / shas[0][:2]).exists(), '空目录也应清理')

    def test_originals_are_never_touched(self):
        folder = self.base / 'photos-on-disk'
        originals = [self.write(folder, n, body) for n, body in (('one.txt', 'alpha'), ('two.txt', 'beta'))]
        sid = self.load(folder, ['one.txt', 'two.txt'], mode='in-place')
        self.assertEqual(self.v.source_impact(sid)['bytes'], 0, '就地索引不占资料库空间')

        self.v.permanently_remove_source(sid)

        for path in originals:
            self.assertTrue(path.is_file(), f'原始文件被删除了：{path}')
            self.assertEqual(path.read_text(encoding='utf-8') in ('alpha', 'beta'), True)
        self.assertEqual(self.v.list_files()['total'], 0)
        self.assertEqual(list((self.v.root / 'objects').rglob('*.*')), [])

    def test_content_shared_with_another_source_survives(self):
        first, second = self.base / 'one', self.base / 'two'
        self.write(first, 'same.md', '共享的内容 identical bytes')
        self.write(second, 'same.md', '共享的内容 identical bytes')
        a = self.load(first, ['same.md'])
        b = self.load(second, ['same.md'])
        sha = self.v.list_files(source=a)['files'][0]['sha']
        self.assertTrue(self.object_path(sha).is_file())

        impact = self.v.source_impact(a)
        self.assertEqual((impact['objects'], impact['shared'], impact['removed']), (1, 1, 0))
        self.assertEqual(self.v.permanently_remove_source(a)['objects'], 0, '仍被引用的对象不能删除')

        self.assertEqual(self.v.list_files()['total'], 1)
        self.assertEqual(self.v.list_files(source=b)['files'][0]['sha'], sha)
        self.assertTrue(self.object_path(sha).is_file())
        self.assertTrue(self.v.detail(self.v.list_files()['files'][0]['id'])['available'])
        self.assertEqual(self.v.list_files(q='共享')['total'], 1, '另一来源仍可被搜索')

    def test_pending_import_job_is_cancelled_with_the_source(self):
        folder = self.base / 'bulk'
        self.write(folder, 'x.md', 'bulk')
        job = self.v.submit_import([str(folder)], 'managed')
        sid = self.v.status()['sources'][0]['id']
        self.assertEqual(len(self.v.status()['jobs']), 1)

        result = self.v.permanently_remove_source(sid)

        self.assertEqual(result['cancelled_jobs'], 1)
        self.assertEqual(self.v.status()['jobs'], [], '断开来源时不能留下会继续写入的任务')
        self.assertNotIn(job['job_id'], [j['id'] for j in self.v.status()['jobs']])

    def test_unknown_source_is_reported_clearly(self):
        with self.assertRaises(ValueError) as caught:
            self.v.permanently_remove_source('does-not-exist')
        self.assertIn('来源不存在', str(caught.exception))
        with self.assertRaises(ValueError):
            self.v.source_impact('does-not-exist')

    def test_manifest_stops_listing_removed_files(self):
        folder = self.base / 'doc'
        self.write(folder, 'keep.md', 'kept')
        other = self.base / 'other'
        self.write(other, 'drop.md', 'dropped')
        self.load(folder, ['keep.md'])
        sid = self.load(other, ['drop.md'])
        self.v.permanently_remove_source(sid)
        manifest = (self.v.root / 'manifest.csv').read_text(encoding='utf-8-sig')
        self.assertIn('keep.md', manifest)
        self.assertNotIn('drop.md', manifest)


class JobDeleteTests(Base):
    def test_deleting_a_queued_job_removes_the_row(self):
        job = self.v.queue('verify', {})
        self.assertEqual(len(self.v.status()['jobs']), 1)
        self.v.delete_job(job['job_id'])
        self.assertEqual(self.v.status()['jobs'], [])

    def test_deleting_a_running_job_stops_it_at_the_next_checkpoint(self):
        jid = 'running-job'
        with self.v.db() as c:
            c.execute("INSERT INTO jobs(id,kind,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (jid, 'import', '{}', 'running', now(), now()))
        self.v._current = jid
        try:
            self.v.checkpoint()  # a live job may check in freely
            self.v.delete_job(jid)
            with self.assertRaises(JobCancelled):
                self.v.checkpoint()
        finally:
            self.v._current = None
        self.assertEqual(self.v.status()['jobs'], [])

    def test_checkpoint_outside_a_job_still_works(self):
        self.v.checkpoint()
        self.v.set_pause(True)
        self.v.stop.set()
        with self.assertRaises(InterruptedError):
            self.v.checkpoint()

    def test_unknown_job_is_reported(self):
        with self.assertRaises(ValueError):
            self.v.delete_job('nope')

    def test_deleting_a_job_keeps_the_imported_files(self):
        folder = self.base / 'keep-me'
        self.write(folder, 'note.md', '内容')
        job = self.v.submit_import([str(folder)], 'managed')
        with self.v.db() as c:
            row = dict(c.execute('SELECT * FROM jobs WHERE id=?', (job['job_id'],)).fetchone())
        self.v.run_job(row)
        self.assertEqual(self.v.list_files()['total'], 1)
        self.v.delete_job(job['job_id'])
        self.assertEqual(self.v.list_files()['total'], 1, '删任务不等于删资料')


if __name__ == '__main__':
    unittest.main()
