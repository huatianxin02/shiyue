"""Persistent sibling ordering and atomic drag reparenting in isolated libraries."""
from contextlib import closing
import json
import sqlite3
import unittest

import test_collection_tree
import test_server
from vault import Vault


class CollectionOrderTests(unittest.TestCase):
    setUp = test_collection_tree.CollectionTreeTests.setUp
    tearDown = test_collection_tree.CollectionTreeTests.tearDown

    def ids(self,parent=None):
        return [row['id'] for row in self.v.collections() if row['parent_id']==parent]

    def test_sibling_order_survives_restart_and_new_items_append(self):
        a,b,c=[self.v.create_collection(name)['id'] for name in ('甲','乙','丙')]
        self.v.move_collection(c,target_id=a,position='before')
        self.assertEqual(self.ids(),[c,a,b])
        self.v.move_collection(c,target_id=b,position='after')
        self.assertEqual(self.ids(),[a,b,c])
        self.v.move_collection(a,target_id=b,position='after')
        self.assertEqual(self.ids(),[b,a,c])
        snapshot=self.v.collections()
        self.v.close();self.v=Vault(self.root/'data',start_worker=False)
        self.assertEqual(self.v.collections(),snapshot)
        d=self.v.create_collection('丁')['id']
        self.assertEqual(self.ids(),[b,a,c,d])

    def test_reparent_branch_inserts_next_to_target_and_preserves_memberships(self):
        root=self.v.create_collection('经学')['id']
        a=self.v.create_collection('甲',root)['id']
        b=self.v.create_collection('乙',root)['id']
        other=self.v.create_collection('书籍')['id']
        leaf=self.v.create_collection('原文',other)['id']
        self.v.assign_collection(leaf,[self.fid])
        self.v.move_collection(other,target_id=b,position='before')
        self.assertEqual(self.ids(root),[a,other,b])
        self.assertEqual(self.v.file_collections(self.fid)[0]['path'],['经学','书籍','原文'])
        self.assertEqual(self.v.file_collections(self.fid)[0]['id'],leaf)
        self.v.move_collection(other,target_id=a,position='inside')
        self.assertEqual(self.ids(root),[a,b])
        self.assertEqual(self.ids(a),[other])
        self.v.move_collection(other,parent_id=None)
        self.assertEqual(self.ids(),[root,other])
        self.assertEqual(self.v.file_collections(self.fid)[0]['path'],['书籍','原文'])

    def test_invalid_drops_are_atomic(self):
        root=self.v.create_collection('甲')['id']
        child=self.v.create_collection('子',root)['id']
        other=self.v.create_collection('乙')['id']
        self.v.create_collection('甲',other)
        before=self.v.collections()
        cases=[dict(target_id=root,position='inside'),dict(target_id=child,position='before'),
               dict(target_id='missing',position='after'),dict(target_id=other,position='inside'),
               dict(target_id=other,position='bogus'),dict(position='before'),
               dict(target_id=other),dict(target_id=other,position='before',parent_id=child)]
        for case in cases:
            with self.subTest(case=case),self.assertRaises(ValueError):
                self.v.move_collection(root,**case)
            self.assertEqual(self.v.collections(),before)

    def test_depth_checked_for_whole_branch_but_level_nine_reorder_allowed(self):
        parent=None
        for i in range(8):parent=self.v.create_collection('层'+str(i),parent)['id']
        a=self.v.create_collection('甲',parent)['id']
        b=self.v.create_collection('乙',parent)['id']
        self.v.move_collection(b,target_id=a,position='before')
        self.assertEqual(self.ids(parent),[b,a])
        before=self.v.collections()
        with self.assertRaisesRegex(ValueError,'9'):self.v.move_collection(a,target_id=b,position='inside')
        branch=self.v.create_collection('新分支')['id']
        self.v.create_collection('子',branch)
        with self.assertRaisesRegex(ValueError,'9'):self.v.move_collection(branch,target_id=a,position='before')
        self.assertEqual(self.ids(parent),[b,a])

    def test_upgrade_preserves_old_sibling_order_and_makes_readable_backup(self):
        root=self.v.create_collection('甲')['id']
        self.v.create_collection('子',root)
        self.v.create_collection('乙')
        with self.v.db() as c:
            old=[tuple(row) for row in c.execute('SELECT id,name,created_at,updated_at,parent_id FROM collections ORDER BY created_at,id')]
            c.execute('ALTER TABLE collections DROP COLUMN sort_order')
            c.execute("UPDATE settings SET value='2' WHERE key='collections_schema'")
        self.v.close();self.v=Vault(self.root/'data',start_worker=False)
        with self.v.db() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT id,name,created_at,updated_at,parent_id FROM collections ORDER BY created_at,id')],old)
            self.assertEqual(c.execute("SELECT value FROM settings WHERE key='collections_schema'").fetchone()[0],'3')
        backups=list((self.v.root/'backups').glob('pre-1.8.1-collections-*.sqlite3'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as c:
            self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertNotIn('sort_order',[r[1] for r in c.execute('PRAGMA table_info(collections)')])
        self.v.init_collections()
        self.assertEqual(list((self.v.root/'backups').glob('pre-1.8.1-collections-*.sqlite3')),backups)


class CollectionOrderHTTPTests(unittest.TestCase):
    setUp=test_server.ServerTests.setUp
    tearDown=test_server.ServerTests.tearDown
    request=test_server.ServerTests.request

    def test_order_route_and_invalid_target_rejected(self):
        def api(path,body=None):
            status,_,raw=self.request(path,body)
            self.assertEqual(status,200,raw)
            return json.loads(raw)
        a=api('/api/collection/create',{'name':'甲'})['id']
        b=api('/api/collection/create',{'name':'乙'})['id']
        payload={'collection_id':b,'target_id':a,'position':'before'}
        api('/api/collection/move',payload)
        self.assertEqual([r['id'] for r in api('/api/collections')['collections']],[b,a])
        self.assertEqual(self.request('/api/collection/move',{**payload,'target_id':'missing'})[0],400)
        self.assertEqual(self.request('/api/collection/move',payload,token=None)[0],403)
        api('/api/collection/move',{**payload,'position':'inside'})
        self.assertEqual(api('/api/collections')['collections'][1]['parent_id'],a)


if __name__=='__main__':unittest.main()
