import json
from contextlib import closing
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from vault import Vault, digest
from knowledge_mcp import Knowledge


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / 'input'
        self.inputs.mkdir()
        self.v = Vault(self.root / 'library', start_worker=False)
        self.sid = self.v.add_source(self.inputs, 'in-place')
        self.v.set_watch(self.sid, True)
        self.path = self.inputs / '第一篇.md'
        self.path.write_text('# 第一节\n\n青山项目预算为 12800 元。\n\n第二段讲述原件管理，保留文件索引。')
        self.fid = self.v.ingest(self.path, self.sid, 'in-place')
        self.k = Knowledge(self.v.root)

    def tearDown(self):
        self.v.close()
        self.temp.cleanup()

    def run_job(self, kind, payload):
        job = self.v.queue(kind, payload)['job_id']
        with self.v.db() as c:
            row = dict(c.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone())
        self.v.run_job(row)
        with self.v.db() as c:
            row = c.execute('SELECT * FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(row['state'], 'done', row['error'])
        return json.loads(row['result'])

    def test_paragraph_offsets_and_stable_context(self):
        hit = self.k.search('原件管理')['results'][0]
        data = self.k.passage(self.fid, hit['passage_id'], 1)
        target = next(p for p in data['passages'] if p['is_target'])
        chunk = self.k.read(self.fid)['chunks'][target['legacy_index']]['text']
        self.assertEqual(chunk[target['chunk_offset']:target['chunk_offset'] + len(target['text'])], target['text'])
        self.assertIn('原件管理', target['text'])
        with self.v.db() as c:
            before = c.execute('SELECT count(*) FROM passages').fetchone()[0]
            self.v._index(c, self.fid)
            self.assertEqual(c.execute('SELECT count(*) FROM passages').fetchone()[0], before)

    def test_rebuild_restores_missing_index_without_changing_fixed_evidence(self):
        hit=self.k.search('原件管理')['results'][0]
        with self.v.db() as c:c.execute('DELETE FROM passage_search');c.execute('DELETE FROM passage_terms')
        self.assertFalse(self.v.knowledge_health()['counts_consistent'])
        self.run_job('rebuild_knowledge',{})
        self.assertTrue(self.v.knowledge_health()['counts_consistent'])
        self.assertEqual(hit['passage_id'],self.k.search('原件管理')['results'][0]['passage_id'])

    def test_word_segmentation_recalls_noncontiguous_chinese_terms(self):
        self.path.write_text('人工智能在产业中的具体应用，需要遵循数据治理规范。')
        self.v.ingest(self.path,self.sid,'in-place')
        hit=self.k.search('人工智能应用')['results'][0]
        self.assertEqual(hit['id'],self.fid)
        self.assertEqual(hit['retrieval'],'word')
        self.assertIn('人工智能',hit['snippet']);self.assertIn('应用',hit['snippet'])

    def test_source_and_collection_intersection_revocation_and_history(self):
        otherdir = self.root / 'secret'
        otherdir.mkdir()
        private = otherdir / 'secret.md'
        private.write_text('青山 绝密正文')
        sid2 = self.v.add_source(otherdir, 'in-place')
        fid2 = self.v.ingest(private, sid2, 'in-place')
        shelf = self.v.create_collection('项目')['id']
        self.v.assign_collection(shelf, [self.fid, fid2])
        profile = self.v.save_knowledge_profile('项目助手', [self.sid], [shelf])['id']
        k = Knowledge(self.v.root, profile)
        self.assertEqual(k.info()['files'], 1)
        self.assertEqual([r['id'] for r in k.search('青山')['results']], [self.fid])
        forbidden = self.k.search('绝密')['results'][0]
        with self.assertRaises(ValueError): k.read(fid2)
        with self.assertRaises(ValueError): k.passage(fid2, forbidden['passage_id'])
        with self.assertRaises(ValueError): k.passage(self.fid, forbidden['passage_id'])
        hit = k.search('青山')['results'][0]
        self.path.write_text('更新的青山预算 20000 元')
        self.v.ingest(self.path, self.sid, 'in-place')
        self.assertTrue(k.passage(self.fid, hit['passage_id'])['historical'])
        self.assertTrue(k.read(self.fid, expected_sha=hit['sha256'], expected_extract=hit['extraction_sha256'])['historical'])
        self.v.save_knowledge_profile('项目助手', [self.sid], [shelf], enabled=False, id=profile)
        for operation in (k.info, lambda:k.search('青山'), lambda:k.read(self.fid), lambda:k.passage(self.fid, hit['passage_id'])):
            with self.assertRaisesRegex(ValueError, '撤销'): operation()

    def test_empty_scope_denies_and_scope_precedes_limit(self):
        empty = self.v.save_knowledge_profile('空范围')['id']
        self.assertEqual(Knowledge(self.v.root, empty).info()['files'], 0)
        self.assertEqual(Knowledge(self.v.root, empty).search('青山')['results'], [])
        with self.assertRaises(ValueError): Knowledge(self.v.root, empty).read(self.fid)
        # More unscoped, better-ranked matches must not displace an allowed match.
        excluded = self.root / 'outside'
        excluded.mkdir()
        sid = self.v.add_source(excluded, 'in-place')
        for n in range(25):
            p = excluded / f'{n}.txt';p.write_text('青山');self.v.ingest(p,sid,'in-place')
        profile = self.v.save_knowledge_profile('仅项目', [self.sid])['id']
        self.assertEqual(Knowledge(self.v.root, profile).search('青山',1)['results'][0]['id'],self.fid)

    def test_notes_conflict_trash_and_backup_restore_history(self):
        note = self.v.note(self.fid, '我的理解：保留原文。')
        with self.assertRaisesRegex(ValueError,'其他窗口'):self.v.note(self.fid,'覆盖')
        note = self.v.note(self.fid,'新版笔记',note['updated_at'])
        hit = self.k.search('青山')['results'][0]
        with self.v.db() as c:
            c.execute('UPDATE objects SET chunks_json=? WHERE sha=?',(json.dumps([{'text':'新的提取结果'}]),hit['sha256']))
            self.v._index(c,self.fid)
        batch=self.v.trash_files([self.fid])
        with self.assertRaises(ValueError):self.k.passage(self.fid,hit['passage_id'])
        self.v.restore_trash(batch['trash_id'])
        self.assertEqual(self.v.note(self.fid)['body'],'新版笔记')
        backup=self.run_job('backup',{})
        restored=self.v.restore_backup(backup['path'])
        other=Vault(restored['path'],start_worker=False)
        try:
            self.assertEqual(other.note(self.fid)['body'],'新版笔记')
            self.assertIn('12800',Knowledge(other.root).passage(self.fid,hit['passage_id'])['passages'][1]['text'])
            with other.db() as c:self.assertFalse(c.execute('SELECT 1 FROM source_watch WHERE enabled=1').fetchone())
        finally:other.close()

    def test_periodic_scan_updates_and_unique_rename_preserves_identity(self):
        self.run_job('import',{'items':[{'path':str(self.inputs),'source_id':self.sid}],'mode':'in-place','incremental':True})
        self.v.edit(self.fid,['保留标签'],True)
        self.v.note(self.fid,'迁移保留笔记')
        moved=self.inputs/'重命名.md';self.path.rename(moved)
        self.v.notify_sources([str(moved)])
        with self.v.db() as c:c.execute('UPDATE source_watch SET next_check=0')
        self.v.watch_tick(force=True)
        with self.v.db() as c:job=dict(c.execute("SELECT * FROM jobs WHERE state='queued' AND kind='import'").fetchone())
        self.v.run_job(job)
        f=self.v.file_row(self.fid)
        self.assertEqual(f['origin_path'],str(moved));self.assertEqual(json.loads(f['tags']),['保留标签'])
        self.assertEqual(self.v.note(self.fid)['body'],'迁移保留笔记')
        moved.write_text('新变化 独特关键词')
        self.run_job('import',{'items':[{'path':str(self.inputs),'source_id':self.sid}],'mode':'in-place','incremental':True})
        self.assertEqual(self.k.search('独特关键词')['results'][0]['id'],self.fid)

    def test_events_during_scan_are_not_lost_and_disabled_source_stays_idle(self):
        self.v.notify_sources([str(self.path)])
        with self.v.db() as c:gen=c.execute('SELECT generation FROM source_watch WHERE source_id=?',(self.sid,)).fetchone()[0]
        self.v.notify_sources([str(self.path)])
        self.v.finish_watch({'auto_source':self.sid,'watch_generation':gen})
        with self.v.db() as c:row=c.execute('SELECT * FROM source_watch WHERE source_id=?',(self.sid,)).fetchone()
        self.assertLess(row['next_check'],time.time()+10)
        self.v.set_watch(self.sid,False)
        self.v.watch_tick(force=True)
        with self.v.db() as c:self.assertIsNone(c.execute("SELECT 1 FROM jobs WHERE state='queued'").fetchone())

    def test_offline_and_missing_files_retain_evidence(self):
        self.path.unlink()
        self.v.finish_watch({'auto_source':self.sid,'watch_generation':0})
        self.assertEqual(self.v.status()['sources'][0]['watch']['missing'],1)
        self.assertIn('12800',self.k.search('12800')['results'][0]['snippet'])
        self.inputs.rmdir()
        with self.v.db() as c:c.execute('UPDATE source_watch SET next_check=0')
        self.v.watch_tick(force=True)
        self.assertIn('离线',self.v.status()['sources'][0]['watch']['last_error'])

    def test_repository_deduplicates_external_originals_and_restores_offline(self):
        repo=self.root/'backups'
        first=self.run_job('repository_backup',{'path':str(repo),'include_external':True})
        second=self.run_job('repository_backup',{'path':str(repo),'include_external':True})
        self.assertGreater(first['added_bytes'],0);self.assertEqual(second['added_bytes'],0)
        self.assertEqual(second['reused_bytes'],first['added_bytes'])
        self.assertEqual(second['external_objects'],0)
        self.path.unlink()
        restored=self.v.restore_backup(second['path'])
        other=Vault(restored['path'],start_worker=False)
        try:
            f=other.file_row(self.fid);self.assertEqual(digest(other._resolve(f)),f['sha'])
            self.assertIn('12800',Knowledge(other.root).read(self.fid)['chunks'][0]['text'])
        finally:other.close()

    def test_repository_corruption_and_changed_original_fail_closed(self):
        repo=self.root/'backups'
        original_sha=digest(self.path)
        self.path.write_text('原件已经改变')
        with self.assertRaisesRegex(ValueError,'变化'):self.v.job_repository_backup('test',{'path':str(repo),'include_external':True})
        self.assertEqual(self.v.repository_list(str(repo)),[])
        self.v.ingest(self.path,self.sid,'in-place')
        result=self.run_job('repository_backup',{'path':str(repo),'include_external':True})
        current=digest(self.path);(repo/'objects'/current[:2]/current).write_text('损坏')
        with self.assertRaisesRegex(ValueError,'校验'):self.v.restore_backup(result['path'])
        with self.assertRaisesRegex(ValueError,'校验'):self.v.job_repository_backup('test',{'path':str(repo),'include_external':True})
        self.assertEqual(self.v.list_files()['total'],1)

    def test_repository_rejects_symlink_and_indexed_target(self):
        with self.assertRaises(ValueError):self.v.repository_plan(str(self.inputs/'backup'))
        repo=self.root/'backups';self.run_job('repository_backup',{'path':str(repo),'include_external':True})
        sha=digest(self.path);target=repo/'objects'/sha[:2]/sha;target.unlink();target.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError,'符号链接'):self.v.repository_plan(str(repo),True)

    def test_migration_is_idempotent_and_snapshot_can_reopen_old_schema(self):
        self.v.close()
        with sqlite3.connect(self.v.dbpath) as c:
            for name in ['passage_terms','passage_search','passages','active_extractions','extractions','knowledge_profiles','file_notes']:
                c.execute('DROP TABLE '+name)
            c.execute("DELETE FROM settings WHERE key='knowledge_schema'")
        self.v=Vault(self.v.root,start_worker=False)
        self.assertEqual(self.k.search('青山')['results'][0]['id'],self.fid)
        with self.v.db() as c:count=c.execute('SELECT count(*) FROM passages').fetchone()[0]
        self.v.close();self.v=Vault(self.v.root,start_worker=False)
        with self.v.db() as c:self.assertEqual(count,c.execute('SELECT count(*) FROM passages').fetchone()[0]);self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        snapshots=list((self.v.root/'backups').glob('pre-1.4.0-*.sqlite3'))
        counts=[]
        for p in snapshots:
            with closing(sqlite3.connect(p)) as c:counts.append(c.execute('SELECT count(*) FROM files').fetchone()[0])
        self.assertIn(1,counts)

    @unittest.skipUnless((Path(__file__).parents[1]/'bin/EventWatch').exists(),'native helper unavailable')
    def test_native_fsevents_reports_real_file_write(self):
        import select
        process=subprocess.Popen([str(Path(__file__).parents[1]/'bin/EventWatch')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            process.stdin.write(json.dumps([str(self.inputs)])+'\n');process.stdin.close()
            ready,_,_=select.select([process.stdout],[],[],8)
            self.assertTrue(ready,'FSEvents did not start')
            self.assertTrue(json.loads(process.stdout.readline()).get('ready'))
            path=self.inputs/'通知验证.txt';path.write_text('FSEvents')
            ready,_,_=select.select([process.stdout],[],[],8)
            self.assertTrue(ready,'FSEvents did not report a real write')
            event=json.loads(process.stdout.readline());self.assertTrue(any(str(self.inputs) in p for p in event['paths']))
        finally:
            process.terminate();process.wait(timeout=3);process.stdout.close()
