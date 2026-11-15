"""Hierarchy limits, existing libraries, evidence scopes and collection round trips."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from collection_tree import MAX_DEPTH
from knowledge_mcp import Knowledge
from vault import Vault
import test_server


class CollectionTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.v=Vault(self.root/'data',start_worker=False)
        self.path=self.root/'正文.md'
        self.path.write_text('卦气研究，预算 12800 元。')
        source=self.v.add_source(self.path,'managed','file')
        self.fid=self.v.ingest(self.path,source,'managed')

    def tearDown(self):
        self.v.close()
        self.temp.cleanup()

    def assign_path(self,path):
        return self.v.assign_collection(None,[self.fid],path=path)['collection']

    def test_nine_levels_and_atomic_path_creation(self):
        path=['第'+str(n)+'级' for n in range(1,10)]
        leaf=self.assign_path(path)
        self.assertEqual(leaf['depth'],MAX_DEPTH)
        self.assertEqual(leaf['path'],path)
        self.assertEqual(self.v.collections()[0]['count'],1)
        with self.assertRaisesRegex(ValueError,'9'):
            self.v.create_collection('第10级',leaf['id'])
        with self.assertRaisesRegex(ValueError,'9'):
            self.assign_path(['新根']+path)
        self.assertEqual(len(self.v.collections()),9)
        repeated=self.v.assign_collection(None,[self.fid],path=path)
        self.assertFalse(repeated['created'])
        self.assertEqual(repeated['changed'],0)
        self.assertEqual(repeated['collection']['id'],leaf['id'])
        sha=self.v.file_row(self.fid)['sha']
        metadata=json.loads((self.v.root/'meta'/sha[:2]/(sha+'.json')).read_text())
        self.assertEqual(metadata['collections'],[' / '.join(path)])
        self.assertIn(' / '.join(path),(self.v.root/'collections.csv').read_text())
        original_db=self.v.db
        @contextmanager
        def limited_db():
            with original_db() as connection:
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,500)
                yield connection
        with patch.object(self.v,'db',limited_db):
            self.v._sync_collection_metadata([self.fid]*1001)

    def test_sibling_uniqueness_and_branch_names(self):
        first=self.assign_path(['易学','经典'])
        second=self.assign_path(['道家','经典'])
        self.assertNotEqual(first['id'],second['id'])
        with self.assertRaisesRegex(ValueError,'同名'):
            self.v.create_collection('经典',first['parent_id'])
        root=self.v.create_collection('AI')
        with self.assertRaises(ValueError):self.v.create_collection('ai')
        self.assertEqual(self.v.assign_collection(None,[self.fid],name='ai')['collection']['id'],root['id'])
        self.assertEqual(len(self.v.file_collections(self.fid)),3)

    def test_parent_filters_deduplicate_and_allow_direct_only(self):
        leaf=self.assign_path(['经学','易学','周易'])
        parent=self.v.collections()[0]
        self.v.assign_collection(parent['id'],[self.fid])
        self.assertEqual(self.v.list_files(collection=parent['id'])['total'],1)
        self.assertEqual(self.v.list_files(collection=leaf['parent_id'],include_children=False)['total'],0)
        self.assertEqual(self.v.list_files(collection=parent['id'],include_children=False)['total'],1)
        self.assertEqual(self.v.collections()[0]['count'],1)
        self.assertEqual(self.v.collections()[0]['direct_count'],1)

    def test_move_preserves_ids_and_blocks_cycles_depth_and_name_conflicts(self):
        leaf=self.assign_path(['易学','经典','周易'])
        before=[row['id'] for row in self.v.collections()]
        target=self.v.create_collection('书籍')
        root_id=before[0]
        moved=self.v.move_collection(root_id,target['id'])
        self.assertEqual(moved['depth'],2)
        self.assertEqual(self.v.file_collections(self.fid)[0]['path'],['书籍','易学','经典','周易'])
        self.assertEqual(self.v.file_collections(self.fid)[0]['id'],leaf['id'])
        sha=self.v.file_row(self.fid)['sha']
        metadata=json.loads((self.v.root/'meta'/sha[:2]/(sha+'.json')).read_text())
        self.assertEqual(metadata['collections'],['书籍 / 易学 / 经典 / 周易'])
        with self.assertRaisesRegex(ValueError,'自身|子分类'):
            self.v.move_collection(target['id'],leaf['id'])
        deep=self.assign_path(['深层']+[str(n) for n in range(2,10)])
        with self.assertRaisesRegex(ValueError,'9'):
            self.v.move_collection(root_id,deep['id'])
        conflict=self.v.create_collection('易学')
        with self.assertRaisesRegex(ValueError,'同名'):
            self.v.move_collection(root_id,None)
        self.v.rename_collection(conflict['id'],'另一个根')
        self.assertEqual(self.v.move_collection(root_id)['depth'],1)

    def test_profiles_include_descendants_and_follow_moves(self):
        leaf=self.assign_path(['经学','易学','周易'])
        root=self.v.collections()[0]
        profile=self.v.save_knowledge_profile('经学范围',collection_ids=[root['id']])['id']
        knowledge=Knowledge(self.v.root,profile)
        hit=knowledge.search('卦气')['results'][0]
        self.assertEqual(hit['id'],self.fid)
        self.assertIn('卦气',knowledge.passage(self.fid,hit['passage_id'])['passages'][0]['text'])
        self.v.move_collection(leaf['id'])
        self.assertEqual(knowledge.search('卦气')['results'],[])
        with self.assertRaises(ValueError):knowledge.read(self.fid)
        self.v.move_collection(leaf['id'],root['id'])
        self.assertEqual(knowledge.search('卦气')['results'][0]['id'],self.fid)

    def test_pack_and_deleted_branch_trash_restore_keep_full_paths(self):
        leaf=self.assign_path(['易学','经典','周易'])
        self.assign_path(['其他','周易'])
        job=self.v.queue('export',{'ids':[self.fid]})
        with self.v.db() as c:row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(job['job_id'],)).fetchone())
        self.v.run_job(row)
        exported=next(j for j in self.v.status()['jobs'] if j['id']==job['job_id'])
        self.assertEqual(exported['state'],'done')
        with zipfile.ZipFile(exported['result']['path']) as archive:
            record=json.loads(archive.read('manifest.json'))['files'][0]
        self.assertEqual(record['collection_paths'],[['其他','周易'],['易学','经典','周易']])
        mount=self.v.queue('mount',{'path':exported['result']['path']})
        with self.v.db() as c:row=dict(c.execute('SELECT * FROM jobs WHERE id=?',(mount['job_id'],)).fetchone())
        self.v.run_job(row)
        self.assertEqual(next(j for j in self.v.status()['jobs'] if j['id']==mount['job_id'])['state'],'done')
        self.assertEqual(next(c for c in self.v.collections() if c['id']==leaf['id'])['count'],2)
        trashed=self.v.trash_files([self.fid])
        root=next(c for c in self.v.collections() if c['path']==['易学'])
        deleted=self.v.delete_collection(root['id'])
        self.assertEqual(deleted['deleted_collections'],3)
        self.assertEqual(self.v.list_files()['total'],1)
        self.v.restore_trash(trashed['trash_id'])
        self.assertEqual({tuple(c['path']) for c in self.v.file_collections(self.fid)},
                         {('其他','周易'),('易学','经典','周易')})
        self.assertEqual(self.path.read_text(),'卦气研究，预算 12800 元。')

    def test_concurrent_creation_and_failed_path_roll_back(self):
        def create():
            try:return self.v.create_collection('同一分类')['id']
            except ValueError:return None
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(lambda _:create(),range(3)))
        self.assertEqual(sum(value is not None for value in results),1)
        with self.v.db() as c:
            c.executemany('INSERT INTO collections(id,name,parent_id) VALUES(?,?,NULL)',
                          [('fixture'+str(n),'根'+str(n)) for n in range(498)])
        with self.assertRaisesRegex(ValueError,'500'):
            self.assign_path(['暂建一级','暂建二级'])
        self.assertEqual(len(self.v.collections()),499)
        self.assertNotIn('暂建一级',[row['name'] for row in self.v.collections()])

    def test_legacy_database_keeps_ids_memberships_and_pre_migration_snapshot(self):
        self.v.close()
        with closing(sqlite3.connect(self.v.dbpath)) as c, c:
            c.execute('DROP INDEX collections_root_name')
            c.execute('DROP INDEX collections_sibling_name')
            c.execute('DROP INDEX collections_parent')
            c.execute('ALTER TABLE collections DROP COLUMN parent_id')
            c.execute('ALTER TABLE collections DROP COLUMN sort_order')
            c.execute("INSERT INTO collections VALUES('legacy-id','旧分类','created','updated')")
            c.execute("INSERT INTO collection_files VALUES('legacy-id',?,'added')",(self.fid,))
            c.execute('CREATE UNIQUE INDEX collections_name ON collections(name COLLATE NOCASE)')
        self.v=Vault(self.v.root,start_worker=False)
        row=self.v.collections()[0]
        self.assertEqual((row['id'],row['name'],row['parent_id'],row['count']),('legacy-id','旧分类',None,1))
        backups=list((self.v.root/'backups').glob('pre-1.8.0-collections-*.sqlite3'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as c:
            self.assertNotIn('parent_id',{r[1] for r in c.execute('PRAGMA table_info(collections)')})
            self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('SELECT collection_id,file_id FROM collection_files').fetchone(),('legacy-id',self.fid))


class CollectionTreeHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request

    def test_create_child_move_and_level_limit_routes(self):
        def api(path,body=None):
            code,_,raw=self.request(path,body)
            self.assertEqual(code,200,raw)
            return json.loads(raw)
        root=api('/api/collection/create',{'name':'书籍'})
        child=api('/api/collection/create',{'name':'易学','parent_id':root['id']})
        self.assertEqual(child['depth'],2)
        self.assertEqual(api('/api/collections')['max_depth'],9)
        moved=api('/api/collection/move',{'collection_id':child['id'],'parent_id':None})
        self.assertEqual(moved['depth'],1)
        self.assertEqual(self.request('/api/collection/move',{'collection_id':root['id'],'parent_id':root['id']})[0],400)
        self.assertEqual(self.request('/api/collections',token=None)[0],403)


if __name__=='__main__':unittest.main()
