"""A selected file must never become an implicit directory or symlink scan."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from disk_discovery import candidate_guard
from vault import Vault


class ImportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.v=Vault(self.root/'library',start_worker=False)
        self.addCleanup(self.v.close)
        self.path=self.root/'selected.md';self.path.write_text('原定入库正文')

    def run_job(self, job):
        with self.v.db() as c:row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(job['job_id'],)).fetchone())
        self.v.run_job(row)
        return self.v.job_receipt(job['job_id'])

    def counts(self):
        with self.v.db() as c:return tuple(c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['sources','jobs','files'])

    def watch_tick(self):
        with patch('source_watch.subprocess.Popen',side_effect=OSError('no helper in unit test')):
            self.v.watch_tick(force=True)

    def test_full_batch_validation_precedes_sources_and_jobs(self):
        for paths in [[str(self.path),str(self.root/'missing')],[str(self.path),str(self.v.dbpath)],'not a list',[42]]:
            with self.assertRaises(ValueError):self.v.submit_import(paths,'in-place')
            self.assertEqual(self.counts(),(0,0,0))
        with self.assertRaises(ValueError):self.v.submit_import([str(self.path)],'in-place','x'*161)
        with self.assertRaises(ValueError):self.v.submit_import([str(self.path)],'in-place',file_guards={})
        self.assertEqual(self.counts(),(0,0,0))

    def test_conflicting_request_rolls_back_new_source(self):
        self.v.submit_import([str(self.path)],'in-place','same-request')
        other=self.root/'other.md';other.write_text('另一份')
        with self.assertRaises(ValueError):self.v.submit_import([str(other)],'in-place','same-request')
        self.assertEqual(self.counts(),(1,1,0))

    def test_guarded_source_never_gets_automatic_unguarded_retry(self):
        guarded=self.v.submit_import([str(self.path)],'in-place','selected-version',file_guards={str(self.path):candidate_guard(self.path)})
        with self.v.db() as c:
            sid=c.execute('SELECT id FROM sources').fetchone()[0]
            self.assertEqual(c.execute('SELECT enabled FROM source_watch WHERE source_id=?',(sid,)).fetchone()[0],0)
        self.path.unlink();self.path.mkdir();(self.path/'unselected.md').write_text('不能扩展授权')
        self.assertEqual(self.run_job(guarded)['state'],'partial')
        self.watch_tick();self.assertEqual(self.counts(),(1,1,0))
        # Explicitly enabling watch still must not convert a file source to a folder.
        self.v.set_watch(sid,True);self.watch_tick();self.assertEqual(self.counts(),(1,1,0))
        with self.v.db() as c:self.assertIn('单文件来源',c.execute('SELECT last_error FROM source_watch WHERE source_id=?',(sid,)).fetchone()[0])

    def test_legacy_single_file_job_cannot_recurse_after_directory_replacement(self):
        job=self.v.submit_import([str(self.path)],'in-place','ordinary')
        self.path.unlink();self.path.mkdir();(self.path/'unselected.md').write_text('不能导入')
        result=self.run_job(job)
        self.assertEqual(result['state'],'partial');self.assertEqual(result['result']['imported'],0)
        self.watch_tick();self.assertEqual(self.counts(),(1,1,0))

    def test_single_file_job_rejects_symlink_in_parent_on_manual_and_auto_rescan(self):
        directory=self.root/'real';directory.mkdir();document=directory/'doc.md';document.write_text('已选择')
        result=self.run_job(self.v.submit_import([str(document)],'in-place','initial'))
        self.assertEqual(result['state'],'done')
        with self.v.db() as c:sid=c.execute('SELECT id FROM sources').fetchone()[0]
        moved=self.root/'moved';directory.rename(moved);directory.symlink_to(moved,target_is_directory=True)
        self.assertEqual(self.run_job(self.v.rescan(sid,True))['state'],'partial')
        self.watch_tick()
        with self.v.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0],0)
        self.assertEqual(self.counts(),(1,2,1))

    def test_existing_file_watch_preference_survives_guarded_import(self):
        self.run_job(self.v.submit_import([str(self.path)],'in-place','first'))
        with self.v.db() as c:sid=c.execute('SELECT id FROM sources').fetchone()[0]
        self.v.set_watch(sid,True)
        self.path.write_text('普通文件的新版本')
        self.run_job(self.v.submit_import([str(self.path)],'in-place','second',file_guards={str(self.path):candidate_guard(self.path)}))
        with self.v.db() as c:self.assertEqual(c.execute('SELECT enabled FROM source_watch WHERE source_id=?',(sid,)).fetchone()[0],1)
        self.assertEqual(self.counts(),(1,2,1))


if __name__=='__main__':unittest.main()
