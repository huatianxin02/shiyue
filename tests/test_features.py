import json, shutil, sqlite3, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from vault import Vault, digest

class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.inputs=self.base/'inputs';self.inputs.mkdir()
        self.v=Vault(self.base/'vault',start_worker=False)
    def tearDown(self):self.v.close();self.temp.cleanup()
    def add(self,name='note.md',text='青山项目资料'):
        p=self.inputs/name
        if name.endswith('.pdf'):
            from pypdf import PdfWriter
            writer=PdfWriter();writer.add_blank_page(width=400,height=500)
            with p.open('wb') as out:writer.write(out)
        else:p.write_text(text)
        sid=self.v.add_source(self.inputs,'managed');return self.v.ingest(p,sid,'managed')
    def run_job(self,kind,payload={}):
        jid=self.v.queue(kind,payload)['job_id']
        with self.v.db() as c:job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        self.v.run_job(job)
        with self.v.db() as c:job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        self.assertEqual(job['state'],'done',job['error']);return json.loads(job['result'])
    def test_trash_restores_search_tags_star_collection_versions_and_reading(self):
        fid=self.add();self.v.edit(fid,['保留标签'],True)
        shelf=self.v.create_collection('项目');self.v.assign_collection(shelf['id'],[fid]);self.v.reading_position(fid,3)
        old=self.v.file_row(fid)['sha'];(self.inputs/'note.md').write_text('新版正文');sid=self.v.status()['sources'][0]['id'];self.v.ingest(self.inputs/'note.md',sid,'managed')
        record=self.v.remove_files([fid]);self.assertEqual(self.v.list_files()['total'],0);self.assertEqual(self.v.list_files(q='新版')['total'],0)
        self.assertTrue((self.v.root/'objects'/old[:2]/old).is_file())
        self.v.restore_trash(record['trash_id']);f=self.v.detail(fid)
        self.assertEqual(f['tags'],['保留标签']);self.assertTrue(f['starred']);self.assertEqual(f['collections'][0]['name'],'项目')
        self.assertEqual(f['versions'][0]['sha'],old);self.assertEqual(self.v.reading_position(fid)['page'],3);self.assertEqual(self.v.list_files(q='新版')['total'],1)
    def test_source_disconnect_and_restore_keeps_source_and_original(self):
        fid=self.add();source=self.v.file_row(fid)['source_id'];batch=self.v.trash_source(source)
        self.assertEqual(len(self.v.status()['sources']),0);self.assertTrue((self.inputs/'note.md').is_file())
        self.v.restore_trash(batch['trash_id']);self.assertEqual(self.v.file_row(fid)['source_id'],source)
    def test_purge_keeps_shared_content_and_other_trash(self):
        one=self.add();two=self.add('copy.txt');sha=self.v.file_row(one)['sha'];path=self.v._resolve(self.v.file_row(one))
        a=self.v.trash_files([one]);b=self.v.trash_files([two]);self.v.purge_trash(a['trash_id']);self.assertTrue(path.exists())
        self.v.restore_trash(b['trash_id']);self.assertEqual(digest(self.v._resolve(self.v.file_row(two))),sha)
        b=self.v.trash_files([two]);self.v.purge_trash(b['trash_id']);self.assertFalse(path.exists());self.assertTrue((self.inputs/'note.md').exists())
    def test_restore_collision_is_atomic_and_keeps_trash(self):
        fid=self.add();batch=self.v.trash_files([fid]);self.add()
        with self.assertRaisesRegex(ValueError,'冲突'):self.v.restore_trash(batch['trash_id'])
        self.assertEqual(len(self.v.trash_list()),1);self.assertEqual(self.v.list_files()['total'],1)
    def test_backup_restore_includes_trash_and_never_overwrites_current_library(self):
        fid=self.add();self.v.edit(fid,['备份'],True);b=self.v.trash_files([fid]);backup=self.run_job('backup')
        restored=self.v.restore_backup(backup['path']);self.assertNotEqual(restored['path'],str(self.v.root));self.assertTrue((Path(restored['path'])/'manifest.csv').is_file())
        other=Vault(restored['path'],start_worker=False)
        try:
            self.assertEqual(len(other.trash_list()),1);other.restore_trash(b['trash_id']);self.assertEqual(other.list_files(q='备份')['total'],1)
            self.assertTrue(other.detail(fid)['starred']);self.assertEqual(digest(other._resolve(other.file_row(fid))),other.file_row(fid)['sha'])
        finally:other.close()
        self.assertEqual(self.v.list_files()['total'],0);self.assertEqual(len(self.v.trash_list()),1)
    def test_backup_rejects_corrupted_bytes_and_cleans_partial_output(self):
        fid=self.add();backup=Path(self.run_job('backup')['path']);bad=self.base/'bad.shiyuebackup'
        with zipfile.ZipFile(backup) as src,zipfile.ZipFile(bad,'w') as dst:
            for name in src.namelist():dst.writestr(name,b'BROKEN' if name.startswith('objects/') else src.read(name))
        with self.assertRaisesRegex(ValueError,'校验'):self.v.restore_backup(bad)
        self.assertEqual(self.v.list_files()['total'],1);self.assertFalse(list(self.base.glob('.shiyue-restore-*')))
    def test_backup_rejects_traversal_even_with_matching_checksum(self):
        import hashlib
        p=self.base/'evil.shiyuebackup';data=b'bad';meta={'format':'shiyue-backup','version':1,'checksums':{'../escape':hashlib.sha256(data).hexdigest(),'library.sqlite3':hashlib.sha256(data).hexdigest()}}
        with zipfile.ZipFile(p,'w') as z:z.writestr('../escape',data);z.writestr('library.sqlite3',data);z.writestr('backup.json',json.dumps(meta))
        with self.assertRaisesRegex(ValueError,'非法'):self.v.restore_backup(p)
        self.assertFalse((self.base.parent/'escape').exists())
    def test_backup_stops_on_corrupted_managed_object(self):
        fid=self.add();path=self.v._resolve(self.v.file_row(fid));path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'损坏'):self.v.job_backup('test',{})
        self.assertFalse(list((self.v.root/'backups').glob('*.shiyuebackup')));self.assertFalse(list((self.v.root/'backups').glob('*.partial')))

    def test_backup_compacts_snapshot_and_compresses_database(self):
        self.add()
        with self.v.db() as c:
            c.execute('CREATE TABLE scratch(payload BLOB)')
            c.execute('INSERT INTO scratch VALUES(zeroblob(4000000))')
        with self.v.db() as c:c.execute('DROP TABLE scratch')
        with self.v.db() as c:before=c.execute('PRAGMA freelist_count').fetchone()[0]
        self.assertGreater(before,500)
        result=self.run_job('backup')
        with zipfile.ZipFile(result['path']) as archive:
            self.assertEqual(archive.getinfo('library.sqlite3').compress_type,zipfile.ZIP_DEFLATED)
            self.assertLess(archive.getinfo('library.sqlite3').file_size,500000)
            meta=json.loads(archive.read('backup.json'));self.assertTrue(meta['database_compacted'])
        with self.v.db() as c:self.assertEqual(c.execute('PRAGMA freelist_count').fetchone()[0],before)
        self.assertLess(result['bytes'],100000)

    def test_backup_records_external_inventory_and_restore_verification(self):
        self.add();path=self.inputs/'external.txt';path.write_text('外部原件不在库内备份')
        sid=self.v.add_source(path,'in-place','file');fid=self.v.ingest(path,sid,'in-place')
        result=self.run_job('backup');self.assertEqual(result['external_files'],1)
        with zipfile.ZipFile(result['path']) as archive:
            meta=json.loads(archive.read('backup.json'));external=meta['external_files'][0]
            self.assertEqual(external['id'],fid);self.assertEqual(external['origin_path'],str(path.resolve()))
            self.assertTrue(external['available_at_backup']);self.assertEqual(len(meta['external_object_manifest']),1)
            self.assertNotIn('objects/'+external['sha256'][:2]+'/'+external['sha256'],archive.namelist())
            self.assertIsNone(meta['last_restore_verification'])
        health=self.v.health();self.assertEqual(health['backup_coverage']['external_files'],1)
        self.assertEqual(health['backup_coverage']['external_bytes'],path.stat().st_size)
        self.assertIsNone(health['last_restore_verification'])
        restored=self.v.restore_backup(result['path']);self.assertTrue(restored['verification_recorded'])
        self.assertEqual(self.v.health()['last_restore_verification']['verified_at'],restored['verified_at'])
        next_backup=self.run_job('backup')
        with zipfile.ZipFile(next_backup['path']) as archive:
            self.assertEqual(json.loads(archive.read('backup.json'))['last_restore_verification']['verified_at'],restored['verified_at'])

    def test_legacy_stored_backup_remains_restorable(self):
        self.add();current=Path(self.run_job('backup')['path']);legacy=self.base/'legacy.shiyuebackup'
        with zipfile.ZipFile(current) as source,zipfile.ZipFile(legacy,'w',zipfile.ZIP_STORED) as target:
            for name in source.namelist():
                data=source.read(name)
                if name=='backup.json':
                    meta=json.loads(data)
                    data=json.dumps({key:meta[key] for key in ('format','version','created_at','external_objects','checksums')}).encode()
                target.writestr(name,data)
        self.assertTrue(Path(self.v.restore_backup(legacy)['path']).is_dir())
    def test_daily_automatic_backup_is_queued_once_and_can_be_disabled(self):
        self.add();self.v.schedule_backup();self.v.schedule_backup()
        with self.v.db() as c:self.assertEqual(c.execute("SELECT count(*) FROM jobs WHERE kind='backup'").fetchone()[0],1)
        with self.v.db() as c:c.execute("INSERT OR REPLACE INTO settings VALUES('auto_backup','0')")
        self.assertFalse(self.v.health()['auto_backup'])
    def test_relocation_checks_hashes_and_keeps_unmatched_records(self):
        one=self.add();two=self.add('changed.txt','old');sid=self.v.file_row(one)['source_id']
        moved=self.base/'moved';shutil.copytree(self.inputs,moved);(moved/'changed.txt').write_text('different');shutil.rmtree(self.inputs)
        preview=self.v.relocate_source(sid,str(moved));self.assertEqual(preview['matched'],1);self.assertEqual(preview['changed'],['changed.txt'])
        self.assertFalse(Path(self.v.file_row(one)['origin_path']).exists())
        result=self.v.relocate_source(sid,str(moved),True);self.assertTrue(result['applied']);self.assertEqual(self.v.file_row(one)['origin_path'],str((moved/'note.md').resolve()))
        self.assertNotEqual(self.v.file_row(two)['origin_path'],str((moved/'changed.txt').resolve()))
    def test_relocation_revalidates_changed_target_on_apply(self):
        fid=self.add();sid=self.v.file_row(fid)['source_id'];dest=self.base/'moved';shutil.copytree(self.inputs,dest)
        self.assertEqual(self.v.relocate_source(sid,str(dest))['matched'],1);(dest/'note.md').write_text('not original')
        with self.assertRaisesRegex(ValueError,'没有哈希'):self.v.relocate_source(sid,str(dest),True)
        self.assertEqual(self.v.file_row(fid)['origin_path'],str((self.inputs/'note.md').resolve()))
    def test_tag_dates_saved_search_and_validation(self):
        one=self.add();self.add('other.md','其他正文');self.v.edit(one,['项目A'],True)
        self.assertEqual(self.v.list_files(tag='项目A')['total'],1);self.assertEqual(self.v.list_files(after='2099-01-01')['total'],0)
        self.assertEqual(self.v.list_files(before='2000-01-01')['total'],0)
        rows=self.v.save_search('项目',{'tag':'项目A','starred':'1'});self.assertEqual(rows[0]['filters']['tag'],'项目A')
        self.v.save_search('项目',{'q':'新关键词'});self.assertEqual(len(self.v.saved_searches()),1)
        with self.assertRaises(ValueError):self.v.save_search('非法',{'sql':'DROP TABLE files'})
    def test_health_distinguishes_offline_unique_and_changed(self):
        fid=self.add();(self.inputs/'note.md').unlink();h=self.v.health();self.assertEqual(len(h['unique']),1);self.assertFalse(h['offline'])
        self.v._resolve(self.v.file_row(fid)).unlink();h=self.v.health();self.assertEqual(len(h['offline']),1);self.assertEqual(len(h['missing']),1)
    def test_incremental_scan_skips_unchanged_but_processes_changed_files(self):
        fid=self.add();sid=self.v.file_row(fid)['source_id'];payload={'items':[{'path':str(self.inputs),'source_id':sid}],'mode':'managed','incremental':True}
        self.run_job('import',payload)
        with patch.object(self.v,'ingest',wraps=self.v.ingest) as ingest:
            self.run_job('import',payload);self.assertEqual(ingest.call_count,0)
            (self.inputs/'note.md').write_text('内容更新');self.run_job('import',payload);self.assertEqual(ingest.call_count,1)
    def test_ocr_indexes_text_without_changing_original_and_preserves_on_refresh(self):
        fid=self.add('scan.pdf','placeholder');sha=self.v.file_row(fid)['sha']
        def probe(id,mode='info',page=1):return {'pages':2} if mode=='info' else {'lines':[{'text':f'识别资料第{page}页','bounds':[0.1,0.2,0.5,0.1]}]}
        with patch.object(self.v,'document_probe',side_effect=probe):self.run_job('ocr',{'id':fid});self.run_job('ocr',{'id':fid})
        f=self.v.detail(fid);self.assertEqual(len([ch for ch in f['chunks'] if ch.get('ocr')]),2)
        self.assertEqual(self.v.list_files(q='识别资料')['total'],1);self.assertEqual(digest(self.v._resolve(self.v.file_row(fid))),sha)
        with patch('vault.extract',return_value=([],'')):self.run_job('refresh',{'force':True})
        self.assertEqual(self.v.list_files(q='识别资料')['total'],1)

    def test_large_source_trash_is_not_limited_by_batch_selection_limit(self):
        fid=self.add();f=self.v.file_row(fid)
        with self.v.db() as c:
            c.executemany("INSERT INTO files(id,sha,source_id,name,origin_path,kind,tags,starred,added_at,updated_at) VALUES(?,?,?,?,?,'document','[]',0,?,?)",[(f'{i:032x}',f['sha'],f['source_id'],f'copy-{i}.txt',str(self.inputs/f'copy-{i}.txt'),f['added_at'],f['updated_at']) for i in range(1001)])
        batch=self.v.remove_source(f['source_id']);self.assertEqual(batch['files'],1002);self.assertEqual(self.v.list_files()['total'],0)
        self.v.restore_trash(batch['trash_id']);self.assertEqual(self.v.list_files()['total'],1002)

import test_server
class FeatureHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request
    def add(self):
        p=self.root/'input.txt';p.write_text('安全恢复资料');sid=self.v.add_source(p,'managed','file');return self.v.ingest(p,sid,'managed')
    def test_new_endpoints_still_require_session_and_origin(self):
        for path,body in [('/api/health',None),('/api/trash',None),('/api/backups',None),('/api/searches',None),('/api/backup',{}),('/api/trash/purge',{'id':'x'})]:
            self.assertEqual(self.request(path,body,token=None)[0],403)
            self.assertEqual(self.request(path,body,origin='https://invalid.example')[0],403)
    def test_file_remove_endpoint_trashes_and_restores_instead_of_unlinking(self):
        fid=self.add();path=self.v._resolve(self.v.file_row(fid))
        code,_,body=self.request('/api/file/remove',{'ids':[fid]});self.assertEqual(code,200)
        bid=json.loads(body)['trash_id'];self.assertTrue(path.exists());self.assertEqual(self.v.list_files()['total'],0)
        self.assertEqual(self.request('/api/trash/restore',{'id':bid})[0],200);self.assertEqual(self.v.list_files()['total'],1)
    def test_backup_settings_search_and_health_routes(self):
        self.add();self.assertEqual(self.request('/api/backup/settings',{'enabled':False})[0],200)
        self.assertFalse(json.loads(self.request('/api/health')[2])['auto_backup'])
        self.assertEqual(self.request('/api/search/save',{'name':'安全','filters':{'q':'安全'}})[0],200)
        rows=json.loads(self.request('/api/searches')[2]);self.assertEqual(rows[0]['filters']['q'],'安全')
        self.assertEqual(self.request('/api/search/delete',{'id':rows[0]['id']})[0],200)
        self.assertEqual(json.loads(self.request('/api/searches')[2]),[])
    def test_paging_and_ocr_reject_invalid_input_without_changing_files(self):
        fid=self.add();self.assertEqual(self.request('/api/reading',{'id':fid,'page':-1})[0],400)
        self.assertEqual(self.request('/api/backup/restore',{'path':str(self.root/'missing')})[0],400)
        self.assertEqual(self.v.list_files()['total'],1)
