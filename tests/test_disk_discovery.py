import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from disk_discovery import DiscoveryService, candidate_guard, validate_candidate
from vault import Vault


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.inputs=self.root/'input';self.inputs.mkdir()
        self.v=Vault(self.root/'library',start_worker=False)
        self.service=DiscoveryService(self.v)

    def tearDown(self):
        self.service.close();self.v.close();self.temp.cleanup()

    def scan(self, paths=None, **kwargs):
        scan=self.service.start(paths or [str(self.inputs)],**kwargs)
        return self.finish(scan['id'])

    def finish(self, scan_id):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            result=self.service.status(scan_id)
            if result['state']!='running':return result
            time.sleep(.01)
        self.fail('discovery did not finish')

    def run_import(self, receipt):
        with self.v.db() as c:job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(receipt['job_id'],)).fetchone())
        self.v.run_job(job)
        return self.v.job_receipt(receipt['job_id'])

    def test_scan_only_metadata_skip_sensitive_trees_and_paginate(self):
        (self.inputs/'报告.md').write_text('内容不可在扫描时读取')
        (self.inputs/'照片.jpg').write_bytes(b'not decoded while discovering')
        (self.inputs/'未知.bin').write_bytes(b'unknown')
        for folder in ('.private','node_modules','Library','程序.app'):
            path=self.inputs/folder;path.mkdir();(path/'不应找到.md').write_text('skip')
        (self.inputs/'链接.md').symlink_to(self.inputs/'报告.md')
        (self.inputs/'目录链接').symlink_to(self.root/'library',target_is_directory=True)
        with patch.object(self.v,'ingest',side_effect=AssertionError('scan cannot ingest')):
            result=self.scan()
        self.assertEqual(result['state'],'done');self.assertEqual(result['total'],3)
        self.assertGreaterEqual(result['skipped'],6)
        self.assertEqual({x['kind'] for x in result['items']},{'document','image','other'})
        page=self.service.status(result['id'],kind='document',q='报告',limit=1)
        self.assertEqual(page['filtered_total'],1);self.assertEqual(page['items'][0]['name'],'报告.md')
        self.assertNotIn('guard',page['items'][0])
        self.assertTrue(self.service.status(result['id'],limit=1)['has_more'])
        with self.v.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sources').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM files').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_only_selected_file_is_imported_and_retries_reuse_receipt(self):
        a=self.inputs/'a.md';a.write_text('选择加入的人工智能报告')
        (self.inputs/'b.md').write_text('未选择的资料')
        result=self.scan();chosen=next(x for x in result['items'] if x['name']=='a.md')
        receipt=self.service.submit(result['id'],[chosen['id']],request_id='one')
        self.assertEqual(self.run_import(receipt)['state'],'done')
        again=self.service.submit(result['id'],[chosen['id']],request_id='one')
        self.assertEqual(receipt['job_id'],again['job_id']);self.assertTrue(again['reused'])
        with self.v.db() as c:
            self.assertEqual([r['origin_path'] for r in c.execute('SELECT origin_path FROM files')],[str(a)])
            self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
        other=next(x for x in result['items'] if x['name']=='b.md')
        with self.assertRaisesRegex(ValueError,'同一请求'):
            self.service.submit(result['id'],[other['id']],request_id='one')
        rescanned=self.scan();self.assertTrue(next(x for x in rescanned['items'] if x['name']=='a.md')['indexed'])

    def test_changed_file_refused_before_submission_and_no_unknown_ids(self):
        path=self.inputs/'a.txt';path.write_text('old')
        result=self.scan();item=result['items'][0]
        with self.assertRaises(ValueError):self.service.submit(result['id'],['invented'])
        path.write_text('new larger version')
        with self.assertRaisesRegex(ValueError,'变化'):
            self.service.submit(result['id'],[item['id']])
        with self.v.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_queue_then_directory_replacement_does_not_import_children(self):
        path=self.inputs/'a.txt';path.write_text('old')
        result=self.scan();receipt=self.service.submit(result['id'],[result['items'][0]['id']])
        path.unlink();path.mkdir();(path/'unselected.txt').write_text('not approved')
        done=self.run_import(receipt)
        self.assertIn(done['state'],('failed','partial'))
        with self.v.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM files').fetchone()[0],0)

    def test_queue_then_parent_symlink_swap_refused(self):
        sub=self.inputs/'sub';sub.mkdir();path=sub/'a.txt';path.write_text('old')
        outside=self.root/'outside';outside.mkdir();(outside/'a.txt').write_text('outside')
        result=self.scan();receipt=self.service.submit(result['id'],[result['items'][0]['id']])
        sub.rename(self.inputs/'original');sub.symlink_to(outside,target_is_directory=True)
        done=self.run_import(receipt);self.assertIn(done['state'],('failed','partial'))
        with self.v.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM files').fetchone()[0],0)

    def test_guards_survive_json_and_detect_inode_even_with_matching_size_mtime(self):
        path=self.inputs/'a.txt';path.write_text('same')
        guard=json.loads(json.dumps(candidate_guard(path,self.inputs)))
        validate_candidate(path,guard)
        path.rename(self.inputs/'old.txt');path.write_text('same')
        os.utime(path,ns=(guard['stat'][3],guard['stat'][3]))
        with self.assertRaisesRegex(ValueError,'变化'):validate_candidate(path,guard)
        link=self.inputs/'link.txt';link.symlink_to(path)
        with self.assertRaises(ValueError):candidate_guard(link)
        with self.assertRaises(ValueError):candidate_guard(self.inputs)
        with self.assertRaises(ValueError):candidate_guard(path,self.root/'elsewhere')

    def test_guard_rejects_real_parent_directory_replacement(self):
        sub=self.inputs/'sub';sub.mkdir();path=sub/'a.txt';path.write_text('same')
        guard=candidate_guard(path)
        sub.rename(self.inputs/'old');sub.mkdir()
        os.link(self.inputs/'old/a.txt',path)
        with self.assertRaisesRegex(ValueError,'目录已变化'):validate_candidate(path,guard)

    def test_limits_cancellation_and_session_expiry(self):
        for i in range(6):(self.inputs/f'{i}.md').write_text('内容')
        limited=self.scan(max_files=2)
        self.assertEqual(limited['state'],'limited');self.assertEqual(limited['total'],2)
        self.assertTrue(limited['limited_reason'])
        with patch('disk_discovery.MAX_ENTRIES',1):
            entry_limit=self.scan();self.assertEqual(entry_limit['state'],'limited')
        with patch('disk_discovery.MAX_SECONDS',0):
            timed=self.scan();self.assertEqual(timed['state'],'limited');self.assertEqual(timed['total'],0)
        gate=threading.Event();original=self.service._scan
        def waiting(*args):gate.wait(3);original(*args)
        with patch.object(self.service,'_scan',side_effect=waiting):
            pending=self.service.start([str(self.inputs)])
            with self.assertRaisesRegex(ValueError,'正在进行'):self.service.start([str(self.inputs)])
            self.service.cancel(pending['id']);gate.set()
            stopped=self.finish(pending['id'])
            self.assertEqual(stopped['state'],'cancelled');self.assertEqual(stopped['total'],0)
        self.scan()
        with self.assertRaisesRegex(ValueError,'过期'):self.service.status(limited['id'])

    def test_vault_itself_and_overlapping_roots(self):
        (self.inputs/'one.txt').write_text('one')
        result=self.scan([str(self.root),str(self.inputs)])
        self.assertEqual(result['total'],1)
        self.assertEqual(result['roots'],[str(self.root)])
        with self.assertRaisesRegex(ValueError,'自身'):self.service.start([str(self.v.root)])
        for args in [([],{}),([str(self.inputs)],{'max_files':True}),([str(self.inputs)],{'max_files':50001})]:
            with self.assertRaises(ValueError):self.service.start(args[0],**args[1])
        with self.assertRaises(ValueError):self.service.status(result['id'],limit=201)
        with self.assertRaises(ValueError):self.service.status(result['id'],kind='bad')
        with self.assertRaises(ValueError):self.service.submit(result['id'],[],mode='in-place')

    def test_mounted_child_volume_is_skipped(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        mounted=self.inputs/'mounted';mounted.mkdir()
        (mounted/'other-volume.txt').write_text('not in requested volume')
        (self.inputs/'local.txt').write_text('within volume')
        real_scandir=os.scandir
        class Entry:
            def __init__(self,entry):self.entry=entry;self.name=entry.name
            def stat(self,**kwargs):
                value=self.entry.stat(**kwargs)
                if self.name=='mounted':
                    return SimpleNamespace(st_dev=value.st_dev+1,st_mode=value.st_mode)
                return value
        @contextmanager
        def volume_entries(fd):
            with real_scandir(fd) as entries:yield (Entry(entry) for entry in entries)
        with patch('disk_discovery.os.scandir',side_effect=volume_entries):result=self.scan()
        self.assertEqual([item['name'] for item in result['items']],['local.txt'])
        self.assertEqual(result['skipped'],1)

    def test_permission_failure_reported_without_dropping_other_results(self):
        good=self.inputs/'good.txt';good.write_text('okay')
        blocked=self.inputs/'blocked';blocked.mkdir();(blocked/'hidden.txt').write_text('skip')
        from disk_discovery import _open_directory
        def fail(path,*args):
            if path==blocked:raise PermissionError('permission denied fixture')
            return _open_directory(path,*args)
        with patch('disk_discovery._open_directory',side_effect=fail):result=self.scan()
        self.assertEqual(result['state'],'done');self.assertEqual(result['errors'],1)
        self.assertEqual(result['items'][0]['path'],str(good))
        self.assertIn('permission denied',result['error_samples'][0]['reason'])


if __name__=='__main__':unittest.main()
