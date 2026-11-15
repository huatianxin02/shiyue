import io,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from vault import Vault
from knowledge_mcp import Knowledge,serve
class KnowledgeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.path=self.root/'原文.md';self.path.write_text('项目合同：青山建设，预算 12800 元。')
  self.v=Vault(self.root/'data',start_worker=False);sid=self.v.add_source(self.path,'in-place','file');self.fid=self.v.ingest(self.path,sid,'in-place');self.k=Knowledge(self.v.root)
 def tearDown(self):self.v.close();self.temp.cleanup()
 def test_search_chinese_tags_and_offline_read_with_citations(self):
  self.v.edit(self.fid,['工程'],False);hit=self.k.search('青山 12800')['results'][0];self.assertEqual(hit['id'],self.fid);self.assertIn('/passages/',hit['citation']);self.assertEqual(len(hit['sha256']),64)
  self.assertEqual(self.k.search('工程')['results'][0]['id'],self.fid);self.path.unlink();doc=self.k.read(self.fid);self.assertFalse(doc['original_available']);self.assertIn('预算',doc['chunks'][0]['text'])
 def test_trash_excluded_and_no_arbitrary_file_paths(self):
  self.v.trash_files([self.fid]);self.assertEqual(self.k.search('合同')['results'],[])
  for id in [self.fid,'../../etc/passwd',str(self.path)]:
   with self.assertRaises(ValueError):self.k.read(id)
 def test_limits_and_query_only_connection(self):
  for n in [0,100,True]:
   with self.assertRaises(ValueError):self.k.search('合同',n)
  with self.assertRaises(ValueError):self.k.read(self.fid,-1)
  c=self.k.db()
  try:
   with self.assertRaises(Exception):c.execute('DELETE FROM files')
  finally:c.close()
  self.assertEqual(self.v.list_files()['total'],1)
 def test_stdio_real_process_initialize_tools_and_read(self):
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'search_library','arguments':{'query':'青山'}}},{'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'read_document','arguments':{'file_id':self.fid}}}]
  p=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'knowledge_mcp.py'),'--data',str(self.v.root)],input='\n'.join(json.dumps(r) for r in requests)+'\n',capture_output=True,text=True,check=True)
  responses=[json.loads(x) for x in p.stdout.splitlines()];self.assertEqual(len(responses),4);self.assertEqual(len(responses[1]['result']['tools']),4);self.assertFalse(responses[2]['result']['isError']);self.assertIn('12800',responses[3]['result']['content'][0]['text'])
 def test_errors_dont_crash_stdio_or_enable_mutation(self):
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'delete_file','arguments':{}}},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'read_document','arguments':{'file_id':'../secret'}}},{'jsonrpc':'2.0','id':4,'method':'ping'}];out=io.StringIO();serve(self.v.root,io.StringIO('\n'.join(json.dumps(r) for r in requests)),out)
  data=[json.loads(x) for x in out.getvalue().splitlines()];self.assertTrue(data[1]['result']['isError']);self.assertTrue(data[2]['result']['isError']);self.assertEqual(data[3]['result'],{})

 def test_version_guard_rejects_stale_citations(self):
  with self.assertRaisesRegex(ValueError,'版本'):self.k.read(self.fid,expected_sha='0'*64)
  self.assertEqual(self.k.read(self.fid,expected_sha=self.v.file_row(self.fid)['sha'])['id'],self.fid)

 def set_chunks(self,chunks):
  with self.v.db() as c:
   c.execute('UPDATE objects SET chunks_json=? WHERE sha=(SELECT sha FROM files WHERE id=?)',(json.dumps(chunks,ensure_ascii=False),self.fid))
   self.v._index(c,self.fid)

 def test_excerpt_centers_late_hit_and_cites_its_actual_chunk(self):
  self.set_chunks([{'text':'开场内容','page':1},{'text':'前文'*700+'青山预算 12800 元。','page':7}])
  hit=self.k.search('青山 12800')['results'][0]
  self.assertEqual(hit['chunk_index'],1);self.assertEqual(hit['page'],7)
  self.assertIn('/passages/',hit['citation']);self.assertIn('青山',hit['snippet'])
  self.assertGreater(hit['snippet_offset'],0);self.assertLessEqual(len(hit['snippet']),1000)
  self.assertEqual(hit['snippet'],self.k.read(self.fid,start=1)['chunks'][0]['text'][hit['snippet_offset']:hit['snippet_offset']+len(hit['snippet'])])

 def test_metadata_only_hit_has_no_fabricated_body_evidence(self):
  self.v.edit(self.fid,['只在标签中'],False)
  hit=self.k.search('只在标签中')['results'][0]
  self.assertEqual(hit['matched_fields'],['tags']);self.assertEqual(hit['snippet'],'')
  self.assertIsNone(hit['chunk_index']);self.assertNotIn('/chunks/',hit['citation'])

 def test_fts_normalized_punctuation_accents_and_unicode_map_to_original(self):
  text='İ 和 '+('前文'*600)+'Café alpha-beta：青，山。'
  self.set_chunks([{'text':''},{'text':text,'page':3}])
  for query,expected in [('cafe','Café'),('alpha-beta','alpha-beta'),('青山','青，山')]:
   with self.subTest(query=query):
    hit=self.k.search(query)['results'][0]
    self.assertEqual(hit['chunk_index'],1);self.assertIn(expected,hit['snippet'])
    self.assertEqual(hit['match_offset'],text.index(expected))

 def test_same_original_reextraction_preserves_historical_citation(self):
  hit=self.k.search('青山')['results'][0]
  self.set_chunks([{'text':'重新切分后的青山正文','page':9}])
  self.assertEqual(hit['sha256'],self.v.file_row(self.fid)['sha'])
  old=self.k.read(self.fid,expected_sha=hit['sha256'],expected_extract=hit['extraction_sha256'])
  self.assertTrue(old['historical']);self.assertIn('12800',old['chunks'][0]['text'])
  fixed=self.k.passage(self.fid,hit['passage_id']);self.assertTrue(fixed['historical']);self.assertIn('12800',fixed['passages'][0]['text'])
  fresh=self.k.search('青山')['results'][0]
  self.assertNotEqual(fresh['extraction_sha256'],hit['extraction_sha256'])
  self.assertEqual(self.k.read(self.fid,expected_sha=fresh['sha256'],expected_extract=fresh['extraction_sha256'])['chunks'][0]['page'],9)

 def test_stdio_accepts_extraction_guard_and_rejects_stale_value(self):
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
   {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'read_document','arguments':{'file_id':self.fid,'expected_extract':'0'*64}}}]
  out=io.StringIO();serve(self.v.root,io.StringIO('\n'.join(json.dumps(r) for r in requests)),out)
  response=json.loads(out.getvalue().splitlines()[1])['result']
  self.assertTrue(response['isError']);self.assertIn('提取版本',response['content'][0]['text'])

import test_server
class KnowledgeHTTPTests(unittest.TestCase):
 setUp=test_server.ServerTests.setUp
 tearDown=test_server.ServerTests.tearDown
 request=test_server.ServerTests.request
 def test_knowledge_routes_require_token_and_block_foreign_origin(self):
  for path in ['/api/knowledge/info','/api/knowledge/config','/api/knowledge/search?q=test','/api/knowledge/read?id=x']:
   self.assertEqual(self.request(path,token=None)[0],403)
   self.assertEqual(self.request(path,origin='https://invalid.example')[0],403)
 def test_search_read_and_current_library_configuration(self):
  p=self.root/'a.txt';p.write_text('AI knowledge source');sid=self.v.add_source(p,'in-place','file');fid=self.v.ingest(p,sid,'in-place')
  result=json.loads(self.request('/api/knowledge/search?q=knowledge')[2]);self.assertEqual(result['results'][0]['id'],fid)
  result=json.loads(self.request('/api/knowledge/read?id='+fid)[2]);self.assertIn('AI knowledge',result['chunks'][0]['text'])
  result=json.loads(self.request('/api/knowledge/config')[2]);self.assertEqual(result['mcpServers']['shiyue']['args'][-1],str(self.v.root))

 def test_http_checks_both_content_and_extraction_versions(self):
  p=self.root/'version.txt';p.write_text('source version');sid=self.v.add_source(p,'in-place','file');fid=self.v.ingest(p,sid,'in-place')
  hit=json.loads(self.request('/api/knowledge/search?q=source')[2])['results'][0]
  base='/api/knowledge/read?id='+fid+'&expected_sha='+hit['sha256']+'&expected_extract='
  self.assertEqual(self.request(base+hit['extraction_sha256'])[0],200)
  self.assertEqual(self.request(base+'0'*64)[0],400)
  self.assertEqual(self.request('/api/knowledge/read?id='+fid+'&expected_sha='+'0'*64)[0],400)
