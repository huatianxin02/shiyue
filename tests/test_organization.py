import io
import json
import unittest
from urllib.parse import urlencode
from knowledge_mcp import Knowledge, serve
from disk_discovery import candidate_guard
import test_disk_discovery
import test_server


class OrganizationTests(unittest.TestCase):
    setUp = test_disk_discovery.DiscoveryTests.setUp
    tearDown = test_disk_discovery.DiscoveryTests.tearDown
    scan = test_disk_discovery.DiscoveryTests.scan
    finish = test_disk_discovery.DiscoveryTests.finish
    run_import = test_disk_discovery.DiscoveryTests.run_import

    def fixture(self, name='成品.md', body='青山卦气共同证据'):
        path = self.inputs / name
        path.write_text(body)
        sid = self.v.add_source(path, 'in-place', 'file')
        return path, self.v.ingest(path, sid, 'in-place')

    def rule(self, **kwargs):
        root = self.v.create_collection('归类目标')
        return self.v.save_classification_rule(name='Markdown 成品', conditions={'extensions':['.md']}, collection_id=root['id'], tags=['状态:成品'], **kwargs)

    def selected(self):
        scan = self.scan()
        ids = [x['id'] for x in scan['items']]
        return scan, ids, self.service.preview(scan['id'], ids)

    def test_filter_before_top_twenty_and_all_search_channels(self):
        ids = [self.fixture(str(i)+'.md')[1] for i in range(21)]
        k = Knowledge(self.v.root)
        top = {x['id'] for x in k.search('青山',20)['results']}
        last = next(x for x in ids if x not in top)
        parent = self.v.create_collection('父分类')
        child = self.v.create_collection('子分类', parent['id'])
        self.v.assign_collection(child['id'],[last])
        self.v.edit(last, ['状态:成品', '载体:Markdown', '仅标签词'])
        for query in ('青山', '共同证据', '青山证据', '仅标签词'):
            hit = k.search(query,1,collection_ids=[parent['id']],tags=['状态:成品','载体:Markdown'],include_paths=True)
            self.assertEqual([r['id'] for r in hit['results']], [last])
            self.assertFalse(hit['post_filtered'])
            self.assertEqual(hit['results'][0]['collections'][0]['path'], ['父分类','子分类'])
            self.assertIn('origin_path',hit['results'][0])
        self.assertEqual(k.search('青山',collection_ids=[parent['id']],include_children=False)['results'], [])
        self.assertEqual(k.search('青山',collection_paths=[['父分类','子分类']])['results'][0]['id'], last)
        self.assertEqual(k.search('青山',tags=['状态:成'])['results'], [])
        self.assertEqual(k.search('青山',collection_paths=[['不存在']])['results'], [])
        self.assertNotIn('origin_path',k.search('青山')['results'][0])
        with self.v.db() as c: self.assertEqual(c.execute('SELECT count(*) FROM files').fetchone()[0],21)

    def test_filters_cannot_expand_profile_and_mcp_validates_fields(self):
        _, a = self.fixture('a.md'); _, b = self.fixture('b.md')
        x = self.v.create_collection('A'); y = self.v.create_collection('B')
        self.v.assign_collection(x['id'],[a]); self.v.assign_collection(y['id'],[b])
        profile = self.v.save_knowledge_profile(name='仅 A',all_files=False,source_ids=[],collection_ids=[x['id']])['id']
        k=Knowledge(self.v.root,profile)
        self.assertEqual(k.search('青山',collection_ids=[y['id']])['results'],[])
        self.assertEqual([r['id'] for r in k.search('青山',collection_ids=[x['id'],y['id']])['results']],[a])
        req=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}, {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'search_library','arguments':{'query':'青山','collection_paths':[['A']],'include_paths':True}}}]
        out=io.StringIO();serve(self.v.root,io.StringIO('\n'.join(json.dumps(r) for r in req)),out,profile=profile)
        result=json.loads(json.loads(out.getvalue().splitlines()[1])['result']['content'][0]['text'])
        self.assertEqual(result['results'][0]['id'],a)
        for options in [{'tags':'成品'},{'collection_ids':['bad']},{'include_paths':1},{'include_children':'false'},{'collection_paths':[['A',1]]},{'tags':['x']*21}]:
            with self.subTest(options=options),self.assertRaises(ValueError):k.search('青山',**options)

    def test_preference_is_explicit_versioned_scope_safe_and_acyclic(self):
        path,a=self.fixture('旧版.md');_,b=self.fixture('成品.md');_,c=self.fixture('其他.md')
        def set_pref(source,target):
            return self.v.set_preference(source,target,'已校对成品',self.v.file_row(source)['sha'],self.v.file_row(target)['sha'])
        set_pref(a,b);set_pref(b,c)
        with self.assertRaisesRegex(ValueError,'循环'):set_pref(c,a)
        with self.assertRaisesRegex(ValueError,'版本'):self.v.set_preference(a,b,'test','0'*64,self.v.file_row(b)['sha'])
        all_results=Knowledge(self.v.root).search('青山')['results']
        self.assertEqual(next(r for r in all_results if r['id']==a)['preferred_file']['id'],b)
        col=self.v.create_collection('仅旧版');self.v.assign_collection(col['id'],[a])
        profile=self.v.save_knowledge_profile(name='仅旧版',all_files=False,source_ids=[],collection_ids=[col['id']])['id']
        hit=Knowledge(self.v.root,profile).search('青山')['results'][0]
        self.assertNotIn('preferred_file',hit)
        old=Knowledge(self.v.root).search('青山')['results'][0]
        path.write_text('青山已更新')
        self.v.ingest(path,self.v.file_row(a)['source_id'],'in-place')
        self.assertFalse(self.v.preference(a)['active'])
        self.assertNotIn('preferred_file',next(r for r in Knowledge(self.v.root).search('青山')['results'] if r['id']==a))
        self.assertEqual(Knowledge(self.v.root).passage(old['id'],old['passage_id'])['id'],old['id'])

    def test_preview_commit_receipt_retry_and_undo_preserve_prior_metadata(self):
        _,fid=self.fixture();prior=self.v.create_collection('已有');self.v.assign_collection(prior['id'],[fid]);self.v.edit(fid,['原有标签'])
        rule=self.rule();scan,ids,preview=self.selected()
        self.assertTrue(preview['can_apply']);self.assertEqual(preview['matched'],1)
        self.assertEqual(self.v.detail(fid)['tags'],['原有标签'])
        receipt=self.service.submit(scan['id'],ids,plan_id=preview['plan_id'])
        with self.assertRaisesRegex(ValueError,'等待'):self.v.undo_classification(preview['plan_id'])
        done=self.run_import(receipt)
        self.assertEqual(done['state'],'done',done)
        result=done['result']['files'][0]['classification'];self.assertTrue(result['verified'])
        self.assertEqual(result['added_tags'],['状态:成品'])
        batch=self.v.classification_batch(preview['plan_id']);self.assertTrue(batch['files'][0]['unchanged_since_apply']);self.assertEqual(batch['files'][0]['added_tags'],['状态:成品'])
        self.assertEqual(self.service.submit(scan['id'],ids,plan_id=preview['plan_id'])['job_id'],receipt['job_id'])
        self.assertEqual(self.run_import(receipt)['state'],'done')
        undo=self.v.undo_classification(preview['plan_id']);self.assertEqual(undo['undone'],[fid])
        detail=self.v.detail(fid);self.assertEqual(detail['tags'],['原有标签']);self.assertEqual([c['id'] for c in detail['collections']],[prior['id']])
        self.assertEqual(self.v.undo_classification(preview['plan_id'])['undone'],[])
        self.assertTrue(self.inputs.joinpath('成品.md').exists())

    def test_rules_and_category_changes_invalidate_preview_then_follow_stable_id(self):
        self.fixture();rule=self.rule();scan,ids,preview=self.selected()
        self.v.rename_collection(rule['collection_id'],'新名称')
        with self.assertRaisesRegex(ValueError,'分类已变化'):self.service.submit(scan['id'],ids,plan_id=preview['plan_id'])
        preview=self.service.preview(scan['id'],ids)
        self.assertEqual(preview['items'][0]['collections'][0]['path'],['新名称'])
        updated=self.v.save_classification_rule(name=rule['name'],conditions=rule['conditions'],collection_id=rule['collection_id'],tags=['状态:修订'],id=rule['id'],expected_version=rule['version'])
        with self.assertRaisesRegex(ValueError,'规则已变化'):self.service.submit(scan['id'],ids,plan_id=preview['plan_id'])
        with self.assertRaisesRegex(ValueError,'版本'):self.v.save_classification_rule(name='stale',conditions=rule['conditions'],tags=['x'],id=rule['id'],expected_version=1)
        self.assertEqual(updated['version'],2)

    def test_file_mutation_and_conflicting_rules_rejected(self):
        path,_=self.fixture();rule=self.rule()
        self.v.save_classification_rule('草稿',{'name_contains':'成品'},tags=['状态:草稿'])
        scan,ids,preview=self.selected();self.assertFalse(preview['can_apply'])
        with self.assertRaisesRegex(ValueError,'冲突'):self.service.submit(scan['id'],ids,plan_id=preview['plan_id'])
        path.write_text('变更后的文件')
        with self.assertRaises(ValueError):self.service.preview(scan['id'],ids)

    def test_undo_skips_subsequent_edits_and_preview_does_not_read_body(self):
        from unittest.mock import patch
        _,fid=self.fixture();self.rule()
        with patch.object(self.v,'ingest',side_effect=AssertionError('preview cannot ingest')):scan,ids,preview=self.selected()
        receipt=self.service.submit(scan['id'],ids,plan_id=preview['plan_id']);done=self.run_import(receipt);self.assertEqual(done['state'],'done',done)
        self.v.edit(fid,['状态:成品','后来添加'])
        undo=self.v.undo_classification(preview['plan_id']);self.assertEqual(undo['undone'],[]);self.assertEqual(len(undo['conflicts']),1)
        self.assertEqual(self.v.detail(fid)['tags'],['状态:成品','后来添加'])

    def test_rules_persist_and_integrity_survives_reopen(self):
        _,fid=self.fixture();rule=self.rule()
        self.v.close()
        from vault import Vault
        self.v=Vault(self.root/'library',start_worker=False)
        self.assertEqual(self.v.classification_rules()[0]['id'],rule['id'])
        with self.v.db() as c:
            self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_full_backup_restores_rules_preferences_and_batch_undo_records(self):
        _,a=self.fixture('旧稿.md');_,b=self.fixture('成品.md');rule=self.rule()
        self.v.set_preference(a,b,'已校对',self.v.file_row(a)['sha'],self.v.file_row(b)['sha'])
        scan,ids,preview=self.selected();self.run_import(self.service.submit(scan['id'],ids,plan_id=preview['plan_id']))
        backup=self.run_import(self.v.queue('backup',{}));self.assertEqual(backup['state'],'done')
        restored=self.v.restore_backup(backup['result']['path'])
        from vault import Vault
        other=Vault(restored['path'],start_worker=False)
        try:
            self.assertEqual(other.classification_rules()[0]['id'],rule['id'])
            self.assertEqual(other.preference(a)['preferred_id'],b)
            self.assertEqual(len(other.classification_batch(preview['plan_id'])['files']),2)
            self.assertEqual(len(other.undo_classification(preview['plan_id'])['undone']),2)
            self.assertEqual(self.v.detail(a)['tags'],['状态:成品'])
        finally:other.close()


class OrganizationHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request

    def test_filter_validation_and_rule_preference_auth(self):
        code,_,body=self.request('/api/knowledge/search?'+urlencode({'q':'青山','tags':json.dumps(['状态:成品']),'include_paths':'true'}))
        self.assertEqual(code,200);self.assertFalse(json.loads(body)['post_filtered'])
        for params in [{'collection_ids':'oops'},{'tags':'"text"'},{'include_children':'maybe'}]:
            code,_,_=self.request('/api/knowledge/search?'+urlencode({'q':'青山',**params}));self.assertEqual(code,400)
        for route in ('/api/classification/rule','/api/classification/delete','/api/classification/undo','/api/files/preference'):
            code,_,_=self.request(route+'?token=test-token',{},token=None);self.assertEqual(code,403)
        code,_,body=self.request('/api/classification/rule',{'name':'Markdown','conditions':{'extensions':['.md']},'tags':['载体:Markdown']})
        self.assertEqual(code,200);rule=json.loads(body)
        code,_,body=self.request('/api/classification/rules');self.assertEqual(json.loads(body)['rules'][0]['id'],rule['id'])
