"""Nine-level logical collections, shared by the UI and authenticated agent APIs."""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import uuid

MAX_DEPTH = 9
MAX_COLLECTIONS = 500


def stamp():
    return datetime.now(timezone.utc).isoformat()


def descendant_query(placeholders='?'):
    return f'''WITH RECURSIVE subtree(id) AS (
        SELECT id FROM collections WHERE id IN ({placeholders})
        UNION SELECT child.id FROM collections child JOIN subtree parent ON child.parent_id=parent.id
    ) SELECT id FROM subtree'''


def collection_rows(connection):
    rows = [dict(row) for row in connection.execute('''
        SELECT c.*,count(cf.file_id) AS direct_count FROM collections c
        LEFT JOIN collection_files cf ON cf.collection_id=c.id
        GROUP BY c.id ORDER BY c.sort_order,c.created_at,c.id''')]
    by_id = {row['id']:row for row in rows}
    children = {}
    for row in rows:
        children.setdefault(row['parent_id'],[]).append(row['id'])
        path = []
        visited = set()
        current = row
        while current:
            if current['id'] in visited or len(path)>=MAX_DEPTH:
                raise ValueError('分类层级异常，请使用升级前备份检查分类结构')
            visited.add(current['id'])
            path.append(current['name'])
            parent = current['parent_id']
            if parent and parent not in by_id:
                raise ValueError('分类的上级记录缺失')
            current = by_id.get(parent)
        row['path'] = list(reversed(path))
        row['path_label'] = ' / '.join(row['path'])
        row['depth'] = len(path)
    totals = dict(connection.execute('''WITH RECURSIVE branch(root,id) AS (
        SELECT id,id FROM collections
        UNION SELECT branch.root,c.id FROM collections c JOIN branch ON c.parent_id=branch.id
    ) SELECT branch.root,count(DISTINCT cf.file_id) FROM branch
      LEFT JOIN collection_files cf ON cf.collection_id=branch.id GROUP BY branch.root'''))
    result = []

    def append_children(parent):
        for cid in children.get(parent,[]):
            row = by_id[cid]
            row['count'] = totals.get(cid,0)
            row['child_count'] = len(children.get(cid,[]))
            result.append(row)
            append_children(cid)

    append_children(None)
    if len(result)!=len(rows):
        raise ValueError('分类结构包含无效分支')
    return result


class CollectionFeatures:
    def init_collections(self):
        with self.db() as connection:
            columns = {r[1] for r in connection.execute('PRAGMA table_info(collections)')}
            legacy = 'parent_id' not in columns
            unordered = 'sort_order' not in columns
            populated = connection.execute('SELECT 1 FROM collections LIMIT 1').fetchone()
        if (legacy or unordered) and populated:
            version = '1.8.0' if legacy else '1.8.1'
            backup = self.root/'backups'/('pre-'+version+'-collections-'+uuid.uuid4().hex[:8]+'.sqlite3')
            with closing(sqlite3.connect(self.dbpath)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        with self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if legacy:
                connection.execute('ALTER TABLE collections ADD COLUMN parent_id TEXT REFERENCES collections(id) ON DELETE CASCADE')
            if unordered:
                connection.execute('ALTER TABLE collections ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
                # Preserve exactly the previous sibling order on upgrade.
                siblings = {}
                for cid,parent in connection.execute('SELECT id,parent_id FROM collections ORDER BY created_at,id').fetchall():
                    order = siblings.get(parent,0)
                    connection.execute('UPDATE collections SET sort_order=? WHERE id=?',(order,cid))
                    siblings[parent] = order+1
            connection.execute('DROP INDEX IF EXISTS collections_name')
            connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS collections_root_name ON collections(name COLLATE NOCASE) WHERE parent_id IS NULL')
            connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS collections_sibling_name ON collections(parent_id,name COLLATE NOCASE) WHERE parent_id IS NOT NULL')
            connection.execute('CREATE INDEX IF NOT EXISTS collections_parent ON collections(parent_id)')
            connection.execute("INSERT OR REPLACE INTO settings VALUES('collections_schema','3')")

    def _collection_name(self, name):
        if not isinstance(name,str):
            raise ValueError('分类名称无效')
        name = ' '.join(name.split())
        if not name or len(name)>40 or any(ord(char)<32 for char in name):
            raise ValueError('分类名称需要 1–40 个字符')
        return name

    def _collection_path(self, path):
        if not isinstance(path,list) or not 1<=len(path)<=MAX_DEPTH:
            raise ValueError('分类路径需要 1–9 级名称')
        return [self._collection_name(name) for name in path]

    def collections(self):
        with self.db() as connection:
            return collection_rows(connection)

    def _collection(self, connection, cid):
        row = connection.execute('SELECT * FROM collections WHERE id=?',(cid,)).fetchone()
        if not row:
            raise ValueError('分类不存在，可能已被删除')
        return dict(row)

    def _collection_depth(self, connection, cid):
        depth = 0
        visited = set()
        while cid:
            if cid in visited or depth>=MAX_DEPTH:
                raise ValueError('分类层级异常')
            visited.add(cid)
            cid = self._collection(connection,cid)['parent_id']
            depth += 1
        return depth

    def _collection_file_ids(self, connection, cid):
        return [row[0] for row in connection.execute(
            'SELECT DISTINCT file_id FROM collection_files WHERE collection_id IN ('+descendant_query()+')',
            (cid,))]

    def _sync_collection_metadata(self, file_ids):
        with self.lock:
            self.manifest()
            if not file_ids:
                return
            objects = set()
            with self.db() as connection:
                for start in range(0,len(file_ids),400):
                    batch = file_ids[start:start+400]
                    objects.update(row[0] for row in connection.execute(
                        'SELECT DISTINCT sha FROM files WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
            for sha in objects:
                self.sidecar(sha)

    def _insert_collection(self, connection, name, parent_id):
        if self._collection_depth(connection,parent_id)>=MAX_DEPTH:
            raise ValueError('分类最多 9 级，当前分类下无法再建子分类')
        if connection.execute('SELECT count(*) FROM collections').fetchone()[0]>=MAX_COLLECTIONS:
            raise ValueError('分类数量已达上限（500 个）')
        if connection.execute('SELECT 1 FROM collections WHERE parent_id IS ? AND name=? COLLATE NOCASE',(parent_id,name)).fetchone():
            raise ValueError(f'同一上级下已有同名分类「{name}」')
        cid = uuid.uuid4().hex
        time = stamp()
        order = connection.execute('SELECT coalesce(max(sort_order),-1)+1 FROM collections WHERE parent_id IS ?',
                                   (parent_id,)).fetchone()[0]
        connection.execute('INSERT INTO collections(id,name,created_at,updated_at,parent_id,sort_order) VALUES(?,?,?,?,?,?)',
                           (cid,name,time,time,parent_id,order))
        return cid

    def _ensure_collection_path(self, connection, path, create=True):
        parent = None
        created = False
        for name in self._collection_path(path):
            row = connection.execute('SELECT id FROM collections WHERE parent_id IS ? AND name=? COLLATE NOCASE',(parent,name)).fetchone()
            if row:
                parent = row[0]
            else:
                if not create:
                    raise ValueError('分类路径不存在')
                parent = self._insert_collection(connection,name,parent)
                created = True
        return parent,created

    def create_collection(self, name, parent_id=None):
        name = self._collection_name(name)
        parent_id = parent_id or None
        with self.lock,self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            cid = self._insert_collection(connection,name,parent_id)
            result = next(row for row in collection_rows(connection) if row['id']==cid)
        self.manifest()
        return result

    def rename_collection(self, cid, name):
        name = self._collection_name(name)
        with self.lock,self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._collection(connection,cid)
            if connection.execute('SELECT 1 FROM collections WHERE parent_id IS ? AND name=? COLLATE NOCASE AND id<>?',
                                  (row['parent_id'],name,cid)).fetchone():
                raise ValueError(f'同一上级下已有同名分类「{name}」')
            connection.execute('UPDATE collections SET name=?,updated_at=? WHERE id=?',(name,stamp(),cid))
            result = next(row for row in collection_rows(connection) if row['id']==cid)
            file_ids = self._collection_file_ids(connection,cid)
        self._sync_collection_metadata(file_ids)
        return result

    def move_collection(self, cid, parent_id=None, target_id=None, position=None):
        """Move an entire branch, optionally beside/inside a target in one transaction.

        Target IDs, not client-supplied positions or tree snapshots, decide the
        destination against the current database. Existing parent-only calls append.
        """
        if target_id is not None or position is not None:
            if not isinstance(target_id,str) or not target_id or position not in ('before','after','inside'):
                raise ValueError('拖动位置需要目标分类和 before / after / inside')
            if parent_id is not None:
                raise ValueError('拖动目标与上级分类只能选择一种')
        parent_id = parent_id or None
        with self.lock,self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._collection(connection,cid)
            descendants = {r[0] for r in connection.execute(descendant_query(),(cid,))}
            if target_id is not None:
                if target_id in descendants:
                    raise ValueError('不能把分类拖到自身或子分类上')
                target = self._collection(connection,target_id)
                parent_id = target_id if position=='inside' else target['parent_id']
            if parent_id in descendants:
                raise ValueError('不能把分类移动到自身或子分类下')
            rows = collection_rows(connection)
            height = max(r['depth'] for r in rows if r['id'] in descendants)-next(r['depth'] for r in rows if r['id']==cid)+1
            if self._collection_depth(connection,parent_id)+height>MAX_DEPTH:
                raise ValueError('移动后分类将超过 9 级，请选择更高的上级')
            if connection.execute('SELECT 1 FROM collections WHERE parent_id IS ? AND name=? COLLATE NOCASE AND id<>?',
                                  (parent_id,row['name'],cid)).fetchone():
                raise ValueError('目标上级下已有同名分类')
            siblings = [r[0] for r in connection.execute(
                'SELECT id FROM collections WHERE parent_id IS ? AND id<>? ORDER BY sort_order,created_at,id',
                (parent_id,cid))]
            index = siblings.index(target_id)+(position=='after') if position in ('before','after') else len(siblings)
            siblings.insert(index,cid)
            connection.execute('UPDATE collections SET parent_id=?,updated_at=? WHERE id=?',(parent_id,stamp(),cid))
            connection.executemany('UPDATE collections SET sort_order=? WHERE id=?',enumerate(siblings))
            if row['parent_id']!=parent_id:
                remaining = [r[0] for r in connection.execute(
                    'SELECT id FROM collections WHERE parent_id IS ? ORDER BY sort_order,created_at,id',(row['parent_id'],))]
                connection.executemany('UPDATE collections SET sort_order=? WHERE id=?',enumerate(remaining))
            result = next(r for r in collection_rows(connection) if r['id']==cid)
            file_ids = self._collection_file_ids(connection,cid)
        self._sync_collection_metadata(file_ids)
        return result

    def delete_collection(self, cid):
        with self.lock,self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._collection(connection,cid)
            subtree = [r[0] for r in connection.execute(descendant_query(),(cid,))]
            file_ids = self._collection_file_ids(connection,cid)
            connection.execute('DELETE FROM collections WHERE id=?',(cid,))
        self._sync_collection_metadata(file_ids)
        return {'name':row['name'],'files':len(file_ids),'deleted_collections':len(subtree)}

    def file_collections(self, fid):
        with self.db() as connection:
            ids = {r[0] for r in connection.execute('SELECT collection_id FROM collection_files WHERE file_id=?',(fid,))}
            return [row for row in collection_rows(connection) if row['id'] in ids]

    def assign_collection(self, cid, ids, action='add', name=None, parent_id=None, path=None):
        if action not in ('add','remove'):
            raise ValueError('无效的分类操作')
        if not ids:
            raise ValueError('请先选择资料')
        ids = self._checked_ids(ids)
        if path is not None:
            path = self._collection_path(path)
            if cid or name or parent_id:
                raise ValueError('完整分类路径与分类 ID/名称只能选择一种')
        created = False
        with self.lock,self.db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            # Verify again in the write transaction before creating any path.
            if connection.execute('SELECT count(*) FROM files WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchone()[0]!=len(ids):
                raise ValueError('所选资料已变化，请重新选择')
            if path is not None:
                cid,created = self._ensure_collection_path(connection,path,create=action=='add')
            elif not cid:
                clean = self._collection_name(name)
                parent_id = parent_id or None
                existing = connection.execute('SELECT id FROM collections WHERE parent_id IS ? AND name=? COLLATE NOCASE',(parent_id,clean)).fetchone()
                if existing:
                    cid = existing[0]
                else:
                    if action=='remove':
                        raise ValueError('分类不存在')
                    cid = self._insert_collection(connection,clean,parent_id)
                    created = True
            self._collection(connection,cid)
            before_memberships = connection.total_changes
            if action=='add':
                connection.executemany('INSERT OR IGNORE INTO collection_files(collection_id,file_id,added_at) VALUES(?,?,?)',
                                       [(cid,fid,stamp()) for fid in ids])
            else:
                connection.execute('DELETE FROM collection_files WHERE collection_id=? AND file_id IN ('+
                                   ','.join('?' for _ in ids)+')',[cid]+ids)
            changed = connection.total_changes-before_memberships
            result = next(row for row in collection_rows(connection) if row['id']==cid)
        self._sync_collection_metadata(ids)
        return {'collection':result,'changed':changed,'action':action,'created':created}
