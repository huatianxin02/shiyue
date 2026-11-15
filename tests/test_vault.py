import hashlib, json, os, sys, tempfile, unittest, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vault import Vault, digest, canonical

class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name); self.inputs=self.base/'inputs';self.inputs.mkdir()
        self.v=Vault(self.base/'vault',start_worker=False)
    def tearDown(self): self.v.close();self.temp.cleanup()
    def put(self,name,text):
        p=self.inputs/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);return p
    def ingest(self,p,mode='managed'):
        sid=self.v.add_source(self.inputs,mode);return self.v.ingest(p,sid,mode)
    def run_job(self,kind,payload):
        jid=self.v.queue(kind,payload)['job_id']
        with self.v.db() as c: job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        self.v.run_job(job)
        with self.v.db() as c: row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        row['result']=json.loads(row['result']) if row['result'] else None
        return row
    def export(self):
        j=self.run_job('export',{});self.assertEqual(j['state'],'done',j['error']);return Path(j['result']['path'])
    def test_copy_dedup_and_chinese_search(self):
        text='项目采购合同：服务器租赁期限十二个月，付款金额 12800 元。'
        p=self.put('合同一.md',text);first=self.ingest(p);self.ingest(self.put('合同备份.txt',text))
        self.assertEqual(self.v.status()['stats']['objects'],1)
        results=self.v.list_files(q='租赁期限');self.assertEqual(results['total'],2)
        self.assertEqual(self.v.list_files(q='租赁 12800')['total'],2)
        self.assertEqual(self.v.list_files(q='不存在')['total'],0)
        p.unlink();f=self.v.detail(first);self.assertTrue(f['available']);self.assertEqual(digest(self.v._resolve(self.v.file_row(first))),f['sha'])
        self.v.edit(first,tags=['商务合同'],starred=True)
        self.assertEqual(self.v.list_files(q='商务合同')['total'],1);self.assertEqual(self.v.list_files(starred=True)['total'],1)
        self.assertTrue((self.v.root/'manifest.csv').is_file())
    def test_update_tracks_revision_and_preserves_original(self):
        p=self.put('合同.txt','第一版旧文本');fid=self.ingest(p);old=self.v.detail(fid)['sha']
        p.write_text('第二版新文本');same=self.ingest(p);self.assertEqual(fid,same)
        self.assertEqual(self.v.list_files(q='旧文本')['total'],0);self.assertEqual(self.v.list_files(q='新文本')['total'],1)
        self.assertEqual(self.v.detail(fid)['versions'][0]['sha'],old)
        self.assertTrue((self.v.root/'objects'/old[:2]/old).exists())
    def test_in_place_offline_retains_search(self):
        p=self.put('照片说明.txt','海边日落与帆船');fid=self.ingest(p,'in-place');p.unlink()
        self.assertFalse(self.v.detail(fid)['available']);self.assertEqual(self.v.list_files(q='帆船')['total'],1)
        j=self.run_job('verify',{});self.assertEqual(j['result']['missing'],1)
        j=self.run_job('export',{});self.assertEqual(j['state'],'failed');self.assertEqual(list((self.v.root/'exports').iterdir()),[])
    def test_single_file_source_rescan_is_scoped(self):
        p=self.put('chosen.txt','选中的文件');self.put('private.txt','未选中的文件')
        submitted=self.v.submit_import([str(p)],'managed')
        with self.v.db() as c: job=dict(c.execute('SELECT * FROM jobs WHERE id=?',(submitted['job_id'],)).fetchone())
        self.v.run_job(job);sid=self.v.status()['sources'][0]['id'];j=self.v.rescan(sid)
        with self.v.db() as c: self.v.run_job(dict(c.execute('SELECT * FROM jobs WHERE id=?',(j['job_id'],)).fetchone()))
        self.assertEqual(self.v.status()['stats']['files'],1)
    def test_changed_source_cannot_be_exported_as_old_hash(self):
        p=self.put('live.txt','old');fid=self.ingest(p,'in-place');p.write_text('changed')
        j=self.run_job('export',{});self.assertEqual(j['state'],'failed')
        j=self.run_job('verify',{});self.assertEqual(len(j['result']['changed']),1)
        self.assertEqual(self.v.detail(fid)['integrity'],'changed')
    def test_missing_managed_object_is_not_hidden_by_original(self):
        p=self.put('kept.txt','content');fid=self.ingest(p);f=self.v.file_row(fid);self.v._resolve(f).unlink()
        self.assertFalse(self.v.detail(fid)['available'])
        self.assertEqual(self.run_job('verify',{})['result']['missing'],1)
    def test_roundtrip_pack_preserves_metadata_and_is_readonly(self):
        fid=self.ingest(self.put('资料.txt','完整包恢复，中文可搜索'))
        self.v.edit(fid,tags=['项目甲','归档'],starred=True);pack=self.export()
        with zipfile.ZipFile(pack) as z:
            manifest=json.loads(z.read('manifest.json'));self.assertNotIn('origin_path',manifest['files'][0])
            self.assertNotIn(str(self.inputs),z.read('manifest.json').decode())
        restored=Vault(self.base/'restored',start_worker=False)
        try:
            result=restored.job_mount('unused',{'path':str(pack)});self.assertTrue(result['verified'])
            found=restored.list_files(q='中文可搜索');self.assertEqual(found['total'],1)
            f=restored.detail(found['files'][0]['id']);self.assertEqual(f['tags'],['项目甲','归档']);self.assertTrue(f['starred']);self.assertTrue(f['available'])
            with self.assertRaisesRegex(ValueError,'只读'): restored.edit(f['id'],tags=['改动'])
            restored.job_mount('unused',{'path':str(pack)});self.assertEqual(restored.status()['stats']['files'],1)
            pack.unlink();self.assertTrue(restored.detail(f['id'])['available'])
        finally: restored.close()
    def test_tampered_pack_rejected_before_registering(self):
        self.ingest(self.put('资料.txt','原始内容'));pack=self.export();bad=self.base/'tampered.vaultpack'
        with zipfile.ZipFile(pack) as src,zipfile.ZipFile(bad,'w') as dst:
            for n in src.namelist(): dst.writestr(n,b'changed' if n.startswith('objects/') else src.read(n))
        other=Vault(self.base/'other',start_worker=False)
        try:
            with self.assertRaisesRegex(ValueError,'校验失败'):other.job_mount('unused',{'path':str(bad)})
            self.assertEqual(other.status()['stats']['files'],0);self.assertEqual(other.status()['sources'],[])
        finally: other.close()
    def test_archive_traversal_rejected(self):
        p=self.base/'bad.vaultpack'
        with zipfile.ZipFile(p,'w') as z:z.writestr('../escape','bad')
        with self.assertRaisesRegex(ValueError,'非法路径'):self.v.job_mount('unused',{'path':str(p)})
        self.assertFalse((self.base/'escape').exists())
    def test_broken_manifest_rejected(self):
        self.ingest(self.put('资料.txt','原始内容'));pack=self.export();bad=self.base/'bad.vaultpack'
        with zipfile.ZipFile(pack) as src,zipfile.ZipFile(bad,'w') as dst:
            for n in src.namelist():dst.writestr(n,b'{}' if n=='manifest.json' else src.read(n))
        with self.assertRaisesRegex(ValueError,'清单校验失败'):self.v.job_mount('unused',{'path':str(bad)})
    def test_office_extraction(self):
        p=self.inputs/'合同.docx'
        with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>付款期限三十天</w:t></w:r></w:p></w:body></w:document>')
        self.ingest(p);self.assertEqual(self.v.list_files(q='三十天')['total'],1)
    def test_restart_recovers_running_job(self):
        q=self.v.queue('verify',{})['job_id'];self.v.job_update(q,state='running');self.v.close()
        self.v=Vault(self.base/'vault',start_worker=False)
        self.assertEqual(self.v.status()['jobs'][0]['state'],'queued')
    def test_own_storage_and_symlinks_not_imported(self):
        with self.assertRaisesRegex(ValueError,'自身'):self.v.submit_import([str(self.v.root)],'managed')
        p=self.put('real.txt','hello');link=self.inputs/'link.txt';link.symlink_to(p)
        with self.assertRaisesRegex(ValueError,'符号链接'):self.v.submit_import([str(link)],'managed')
        self.assertEqual(len(list(self.v.iter_files(self.inputs))),1)
    def test_user_input_cannot_break_search_or_store_markup(self):
        p=self.put('<hello>.txt','引号 " OR * <script> 内容');self.ingest(p)
        for query in ['" OR *','付款 OR','<script>','!!!','a\'b']:
            self.assertIsInstance(self.v.list_files(q=query)['total'],int)
    def test_pagination(self):
        sid=self.v.add_source(self.inputs)
        for i in range(67): self.v.ingest(self.put(f'{i}.txt',f'文件内容 {i}'),sid,'managed')
        a=self.v.list_files();b=self.v.list_files(offset=60)
        self.assertEqual(len(a['files']),60);self.assertEqual(len(b['files']),7)
        self.assertFalse({f['id'] for f in a['files']} & {f['id'] for f in b['files']})

if __name__=='__main__':unittest.main()
