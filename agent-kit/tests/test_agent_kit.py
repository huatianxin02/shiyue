"""Exercise the portable agent kit against an isolated source HTTP server with bundled Python.

No live library, direct database access, GUI, or remote service is used.
Run with the installed app's Python and -B.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import plistlib

APP = Path(os.environ['SHIYUE_TEST_APP']).resolve()
PYTHON = APP / 'Contents/Resources/python/bin/python3'
RUNTIME = APP / 'Contents/Resources/runtime'
PROJECT = RUNTIME
KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_ingest_client', RUNTIME / 'agent_ingest.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AgentKitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='shiyue-agent-kit-')
        cls.root = Path(cls.temp.name).resolve()
        cls.data = cls.root / '隔离资料库'
        cls.kit = cls.root / 'Agent 工具' / 'shiyue_agent.py'
        cls.kit.parent.mkdir()
        shutil.copy2(KIT / 'shiyue_agent.py', cls.kit)
        cls.env = {**os.environ, 'PATH':'/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE':'1'}
        cls.log = (cls.root / 'server.log').open('w+')
        cls.process = subprocess.Popen([str(PYTHON), '-B', str(PROJECT / 'server.py'),
            '--data', str(cls.data)], stdout=cls.log, stderr=cls.log, env=cls.env)
        cls.addClassCleanup(cls.cleanup_server)
        cls.client = module.Client(cls.data)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError('Isolated server failed: ' + cls.log.read()[-2000:])
            try:
                cls.client.request('/api/status')
                return
            except ValueError:
                time.sleep(.05)
        raise RuntimeError('Isolated server readiness timed out')

    @classmethod
    def cleanup_server(cls):
        cls.process.terminate()
        try: cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)
        cls.log.close()
        cls.temp.cleanup()

    def call(self, *args, code=0):
        process = subprocess.run([str(PYTHON), '-B', str(self.kit), '--app', str(APP), '--data', str(self.data),
            *map(str,args)], capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        self.assertNotIn('Traceback', process.stderr)
        value = json.loads(process.stdout)
        self.assertEqual(value['ok'], code == 0)
        token = json.loads((self.data / 'runtime.json').read_text())['token']
        self.assertNotIn(token, process.stdout + process.stderr)
        return value

    def ingest(self, paths, request):
        process = subprocess.run([str(PYTHON), '-B', str(RUNTIME / 'agent_ingest.py'),
            '--data', str(self.data), '--request-id', request, '--wait', '20', *map(str,paths)],
            capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        receipt = json.loads(process.stdout)
        self.assertTrue(receipt['index_ready'])
        return receipt

    def fixture(self):
        folder = self.root / self._testMethodName
        folder.mkdir()
        paths = [folder/'青山读本.md', folder/'青山读本.html']
        paths[0].write_text('# 青山读本\n\n卦气研究计划，预算 12800 元。共时性用于对照解释。', encoding='utf-8')
        paths[1].write_text('<html><body><h1>青山读本</h1><p>卦气研究计划，预算 12800 元。</p></body></html>', encoding='utf-8')
        receipt = self.ingest(paths, self._testMethodName+'-v1')
        saved = folder/'入库回执.json'
        saved.write_text(json.dumps(receipt,ensure_ascii=False),encoding='utf-8')
        return paths, receipt, saved

    def test_classification_preserves_metadata_and_is_idempotent(self):
        paths, receipt, saved = self.fixture()
        ids = [f['id'] for f in receipt['files']]
        original = [p.read_bytes() for p in paths]
        self.client.request('/api/collection/assign',{'name':'已有分类','ids':ids,'action':'add'})
        for fid in ids: self.client.request('/api/edit',{'id':fid,'tags':['原有标签']})
        arguments = ('classify','--receipt',saved,'--collection','Kit AI','--tag','书目:青山读本')
        self.assertFalse(self.call(*arguments)['applied'])
        self.assertNotIn('Kit AI', [c['name'] for c in self.client.request('/api/status')['collections']])
        applied = self.call(*arguments,'--apply')
        self.assertTrue(applied['verified'])
        for item in applied['files']:
            self.assertEqual(set(item['tags']), {'原有标签','书目:青山读本'})
            self.assertEqual({c['name'] for c in item['collections']}, {'已有分类','Kit AI'})
        again = self.call(*arguments,'--apply')
        self.assertTrue(all(x.get('changed') == 0 for x in again['changes']))
        # The service reuses ASCII case-insensitive collection names.
        reused = self.call('classify','--id',ids[0],'--collection','kit ai','--apply')
        self.assertTrue(reused['verified'])
        self.assertEqual(original, [p.read_bytes() for p in paths])

    def test_find_uses_full_path_and_supports_long_names(self):
        paths, receipt, _ = self.fixture()
        found = self.call('find','--path',paths[0])
        self.assertEqual(found['count'],1)
        fid = found['matches'][0]['id']
        self.assertIn(fid, [f['id'] for f in receipt['files']])
        self.assertEqual(self.call('find','--path',paths[0].parent/'missing.md')['count'],0)
        long_file = paths[0].parent/('a'*205+'.txt')
        long_file.write_text('长文件名检索',encoding='utf-8')
        long_receipt = self.ingest([long_file],self._testMethodName+'-long')
        self.assertEqual(self.call('find','--path',long_file)['matches'][0]['id'],long_receipt['files'][0]['id'])
        inspected = self.call('inspect','--id',fid)['files'][0]
        self.assertEqual(inspected['origin_path'], str(paths[0]))
        self.assertNotIn('chunks', inspected)

    def test_stale_receipt_and_missing_id_make_no_changes(self):
        paths, receipt, saved = self.fixture()
        paths[0].write_text('青山读本修订版本：预算改为 25600 元。',encoding='utf-8')
        self.ingest([paths[0]],self._testMethodName+'-v2')
        error = self.call('classify','--receipt',saved,'--collection','不得创建过期分类','--apply',code=2)
        self.assertIn('更新',error['error'])
        fid = receipt['files'][0]['id']
        self.call('classify','--id',fid,'--id','0'*32,'--collection','不得创建缺失分类','--apply',code=2)
        names = [c['name'] for c in self.client.request('/api/status')['collections']]
        self.assertNotIn('不得创建过期分类',names)
        self.assertNotIn('不得创建缺失分类',names)

    def test_invalid_receipts_and_tag_overflow_fail_before_writing(self):
        _, receipt, saved = self.fixture()
        invalid = [[], {'success':True}, {**receipt,'files':[None]},
                   {**receipt,'complete':False}, {**receipt,'failure_count':1}]
        for value in invalid:
            saved.write_text(json.dumps(value),encoding='utf-8')
            self.call('classify','--receipt',saved,'--collection','不得创建非法分类','--apply',code=2)
        fid = receipt['files'][0]['id']
        self.client.request('/api/edit',{'id':fid,'tags':['tag'+str(i) for i in range(50)]})
        self.call('classify','--id',fid,'--collection','不得创建超限分类','--tag','第51个','--apply',code=2)
        names = [c['name'] for c in self.client.request('/api/status')['collections']]
        self.assertNotIn('不得创建非法分类',names)
        self.assertNotIn('不得创建超限分类',names)

    def test_search_passage_and_versioned_read_obey_profile(self):
        _, receipt, _ = self.fixture()
        fid = receipt['files'][0]['id']
        allowed = self.client.request('/api/collection/assign',{'name':'读取范围测试','ids':[fid],'action':'add'})['collection']['id']
        profile = self.client.request('/api/knowledge/profile',{'name':'仅测试书目','collection_ids':[allowed]})['id']
        result = self.call('search','--query','卦气','--profile',profile)['results']
        self.assertEqual([r['id'] for r in result],[fid])
        hit = result[0]
        self.assertIn('卦气',hit['snippet'])
        passages = self.call('passage','--id',fid,'--passage',hit['passage_id'],'--profile',profile)['passages']
        self.assertTrue(any('卦气' in p['text'] for p in passages))
        reading = self.call('read','--id',fid,'--sha',hit['sha256'],'--extract',hit['extraction_sha256'],'--profile',profile)
        self.assertTrue(any('卦气' in c['text'] for c in reading['chunks']))
        self.call('read','--id',fid,'--sha','0'*64,'--profile',profile,code=2)
        blocked = self.client.request('/api/knowledge/profile',{'name':'空范围'})['id']
        self.assertEqual(self.call('search','--query','卦气','--profile',blocked)['results'],[])
        self.call('read','--id',fid,'--profile',blocked,code=2)
        self.call('passage','--id',fid,'--passage',hit['passage_id'],'--profile',blocked,code=2)

    def test_agent_can_classify_full_path_create_and_move_child(self):
        _, receipt, saved = self.fixture()
        path=json.dumps(['易学','经典','周易'],ensure_ascii=False)
        outcome=self.call('classify','--receipt',saved,'--collection-path',path,'--apply')
        self.assertTrue(outcome['verified'])
        self.assertEqual(outcome['files'][0]['collections'][0]['path'],['易学','经典','周易'])
        root=self.call('collection-create','--name','独立目录')['collection']
        child=self.call('collection-create','--name','子目录','--parent-id',root['id'])['collection']
        self.assertEqual(child['depth'],2)
        moved=self.call('collection-move','--id',child['id'],'--root')['collection']
        self.assertEqual(moved['depth'],1)
        self.call('classify','--id',receipt['files'][0]['id'],'--collection-id',child['id'],'--apply')
        self.call('classify','--id',receipt['files'][0]['id'],'--collection-path','[1]','--apply',code=2)

    def test_status_and_offline_error_are_actionable(self):
        status = self.call('status')
        self.assertRegex(status['app_version'],r'^1\.(?:9|10)\.\d+$')
        self.assertTrue(status['write_compatible'])
        self.assertEqual(status['collection_max_depth'],9)
        self.assertEqual(status['data_path'],str(self.data))
        process = subprocess.run([str(PYTHON),'-B',str(self.kit),'--app',str(APP),'--data',str(self.root/'offline'),'status'],
            capture_output=True,text=True,env=self.env,timeout=10)
        self.assertEqual(process.returncode,2)
        self.assertIn('先打开拾页',json.loads(process.stdout)['error'])

    def test_server_filters_recover_twenty_first_document_and_scope_is_preserved(self):
        folder=self.root/self._testMethodName;folder.mkdir()
        paths=[]
        for i in range(21):
            path=folder/(str(i)+'.md');path.write_text('便携筛选专用证据。青山预算 98127 元。');paths.append(path)
        receipt=self.ingest(paths,self._testMethodName)
        top=self.call('search','--query','便携筛选专用证据','--limit','20','--profile','default')
        ids={r['id'] for r in top['results']}
        last=next(r['id'] for r in receipt['files'] if r['id'] not in ids)
        self.call('classify','--id',last,'--collection-path','["便携测试","目标"]','--tag','状态:成品','--apply')
        result=self.call('search','--query','便携筛选专用证据','--limit','1','--profile','default','--collection-path','["便携测试"]','--tag','状态:成品','--include-paths')
        self.assertEqual([r['id'] for r in result['results']],[last]);self.assertFalse(result['post_filtered'])
        hit=result['results'][0];self.assertEqual(hit['collections'][0]['path'],['便携测试','目标']);self.assertIn('origin_path',hit)
        evidence=self.call('passage','--id',last,'--passage',hit['passage_id'],'--profile','default')
        self.assertEqual(evidence['sha256'],hit['sha256']);self.assertEqual(evidence['extraction_sha256'],hit['extraction_sha256'])
        self.assertTrue(any(p['is_target'] and '98127' in p['text'] for p in evidence['passages']))
        blocked=self.client.request('/api/knowledge/profile',{'name':'不开放','all_files':False,'source_ids':[],'collection_ids':[]})['id']
        self.assertEqual(self.call('search','--query','便携筛选专用证据','--profile',blocked,'--collection-path','["便携测试"]')['results'],[])
        self.assertEqual(self.call('search','--query','便携筛选专用证据','--profile','default','--collection-path','["便携测试"]','--direct-only')['results'],[])

    def test_preference_requires_explicit_apply_and_preserves_old_evidence(self):
        _,receipt,_=self.fixture();ids=[f['id'] for f in receipt['files']]
        args=('prefer','--id',ids[0],'--preferred-id',ids[1],'--reason','测试已校对成品')
        self.assertFalse(self.call(*args)['applied'])
        applied=self.call(*args,'--apply');self.assertTrue(applied['preference']['active'])
        self.assertEqual(self.call('inspect','--id',ids[0])['files'][0]['preference']['preferred_id'],ids[1])
        self.call('prefer','--id',ids[1],'--preferred-id',ids[0],'--reason','禁止循环','--apply',code=2)
        self.assertIsNone(self.call('prefer','--id',ids[0],'--clear','--apply')['preference'])

    def test_unknown_version_or_missing_capability_stays_read_only(self):
        spec=importlib.util.spec_from_file_location('guard_kit',self.kit)
        kit=importlib.util.module_from_spec(spec);spec.loader.exec_module(kit)
        class UnknownClient:
            def __init__(self,status):self.status=status;self.writes=[]
            def request(self,route,payload=None):
                if payload is not None:self.writes.append(route)
                return self.status
        for status in [{'version':'2.0.0','capabilities':{'collection_tree':1}},{'version':'1.9.1','capabilities':{}}]:
            client=UnknownClient(status)
            with self.assertRaisesRegex(ValueError,'只读'):kit.run(kit.parser().parse_args(['collection-create','--name','阻止写入']),client)
            self.assertEqual(client.writes,[])

    def test_partial_failure_reports_applied_steps(self):
        # Simulate a lost connection between collection assignment and tag update.
        spec = importlib.util.spec_from_file_location('kit_under_test',self.kit)
        kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kit)
        fid='a'*32
        class DisconnectingClient:
            def __init__(self):self.reads=0
            def request(self,route,payload=None):
                if route=='/api/status':return {'version':'1.9.0','capabilities':{'classify':1}}
                if route.startswith('/api/files/'):
                    self.reads+=1
                    if self.reads>1:raise ValueError('无法连接拾页')
                    return {'id':fid,'sha':'b'*64,'mode':'in-place','tags':[],'collections':[]}
                return {'collection':{'id':'c'*32,'name':'测试分类'},'changed':1}
        args=kit.parser().parse_args(['classify','--id',fid,'--collection','测试分类','--tag','新标签','--apply'])
        outcome=kit.run(args,DisconnectingClient())
        self.assertFalse(outcome['ok'])
        self.assertTrue(outcome['applied'])
        self.assertFalse(outcome['verified'])
        self.assertEqual(len(outcome['completed_steps']),1)
        self.assertIn('inspect',outcome['notice'])


if __name__=='__main__':unittest.main(verbosity=2)
