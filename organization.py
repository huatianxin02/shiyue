"""Explicit preferred copies and previewed, additive scan classification rules."""
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from collection_tree import collection_rows


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('需要完整 ID')
    return value


def tags_list(tags):
    if not isinstance(tags, list) or len(tags) > 50 or any(not isinstance(t, str) or not t.strip() or len(t) > 80 or any(ord(ch) < 32 for ch in t) for t in tags):
        raise ValueError('标签须为最多 50 个、每个 1–80 字的名称')
    return list(dict.fromkeys(t.strip() for t in tags))


def validate_conditions(conditions):
    allowed = {'path_contains', 'path_prefix', 'name_contains', 'extensions', 'kind', 'size_min', 'size_max', 'modified_after', 'modified_before'}
    if not isinstance(conditions, dict) or not conditions or set(conditions) - allowed:
        raise ValueError('请至少设置一个支持的匹配条件')
    result = dict(conditions)
    for key, value in result.items():
        if key in ('size_min', 'size_max'):
            if type(value) is not int or not 0 <= value <= 10**15:
                raise ValueError('文件大小条件无效')
        elif key == 'extensions':
            if not isinstance(value, list) or not 1 <= len(value) <= 30 or any(not isinstance(x, str) or not re.fullmatch(r'\.[A-Za-z0-9]{1,16}', x) for x in value):
                raise ValueError('扩展名须为数组，例如 [".pdf", ".md"]')
            result[key] = list(dict.fromkeys(x.lower() for x in value))
        else:
            if not isinstance(value, str) or not value.strip() or len(value) > 500 or any(ord(ch) < 32 for ch in value):
                raise ValueError('匹配条件无效')
            if key == 'kind' and value not in ('document', 'image', 'video', 'audio', 'other'):
                raise ValueError('文件类型无效')
            if key.startswith('modified_'):
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): raise ValueError()
                    datetime.strptime(value, '%Y-%m-%d')
                except ValueError: raise ValueError('日期须为有效的 YYYY-MM-DD（UTC 修改日期）')
            if key == 'path_prefix':
                if not Path(value).is_absolute(): raise ValueError('目录前缀须为绝对路径')
                result[key] = str(Path(value))
    if result.get('size_min', 0) > result.get('size_max', 10**15):
        raise ValueError('大小条件下限大于上限')
    if result.get('modified_after', '') > result.get('modified_before', '9999-12-31'):
        raise ValueError('修改日期范围无效')
    return result


def matches(item, conditions):
    for key, value in conditions.items():
        if key == 'path_contains' and value.casefold() not in item['path'].casefold(): return False
        if key == 'name_contains' and value.casefold() not in item['name'].casefold(): return False
        if key == 'path_prefix' and item['path'] != value and not item['path'].startswith(value.rstrip('/') + '/'): return False
        if key == 'extensions' and Path(item['name']).suffix.lower() not in value: return False
        if key == 'kind' and item['kind'] != value: return False
        if key == 'size_min' and item['size'] < value: return False
        if key == 'size_max' and item['size'] > value: return False
        if key.startswith('modified_'):
            date = datetime.fromtimestamp(item['mtime_ns'] / 10**9, timezone.utc).strftime('%Y-%m-%d')
            if key == 'modified_after' and date < value: return False
            if key == 'modified_before' and date > value: return False
    return True


class OrganizationFeatures:
    def init_organization(self):
        with self.db() as c:
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='classification_rules'").fetchone()
            if not exists and c.execute('SELECT 1 FROM files LIMIT 1').fetchone():
                target = self.root / 'backups' / ('pre-1.9.0-organization-' + uuid.uuid4().hex[:12] + '.sqlite3')
                target.parent.mkdir(exist_ok=True)
                with closing(sqlite3.connect(target)) as backup:
                    c.backup(backup)
            c.executescript('''
                CREATE TABLE IF NOT EXISTS file_preferences(
                    file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
                    preferred_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    source_sha TEXT NOT NULL,target_sha TEXT NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS classification_rules(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL,
                    conditions TEXT NOT NULL,collection_id TEXT,tags TEXT NOT NULL,
                    priority INTEGER NOT NULL,enabled INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS classification_plans(
                    id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL,job_id TEXT,mode TEXT);
                CREATE TABLE IF NOT EXISTS classification_changes(
                    batch_id TEXT NOT NULL REFERENCES classification_plans(id),file_id TEXT NOT NULL,
                    sha TEXT NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL,undone_at TEXT,
                    PRIMARY KEY(batch_id,file_id));
            ''')

    def preference(self, fid):
        with self.db() as c:
            row = c.execute('''SELECT p.*,f.name AS preferred_name,f.sha AS current_target_sha,s.sha AS current_source_sha
                FROM file_preferences p JOIN files f ON f.id=p.preferred_id JOIN files s ON s.id=p.file_id WHERE p.file_id=?''', (fid,)).fetchone()
        if not row: return None
        result = dict(row)
        result['active'] = result['source_sha'] == result.pop('current_source_sha') and result['target_sha'] == result['current_target_sha']
        result.pop('current_target_sha')
        result['specified_by'] = 'owner'
        return result

    def set_preference(self, file_id, preferred_id=None, reason='', expected_sha=None, target_sha=None):
        identifier(file_id)
        if preferred_id is not None: identifier(preferred_id)
        if not isinstance(reason, str) or len(reason) > 300 or (preferred_id and not reason.strip()):
            raise ValueError('请填写 1–300 字的选择理由')
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            source = c.execute('SELECT sha FROM files WHERE id=?', (file_id,)).fetchone()
            if not source or source[0] != expected_sha: raise ValueError('来源文件版本已变化，请重新核对')
            if preferred_id:
                target = c.execute('SELECT sha FROM files WHERE id=?', (preferred_id,)).fetchone()
                if not target or target[0] != target_sha: raise ValueError('优先文件版本已变化，请重新核对')
                seen, current = {file_id}, preferred_id
                while current:
                    if current in seen: raise ValueError('优先条目关系不能指向自身或形成循环')
                    seen.add(current)
                    row = c.execute('SELECT preferred_id FROM file_preferences WHERE file_id=?', (current,)).fetchone()
                    current = row[0] if row else None
                c.execute('INSERT OR REPLACE INTO file_preferences VALUES(?,?,?,?,?,?)', (file_id, preferred_id, expected_sha, target_sha, reason.strip(), stamp()))
            else:
                c.execute('DELETE FROM file_preferences WHERE file_id=?', (file_id,))
        return {'file_id': file_id, 'preference': self.preference(file_id)}

    def classification_rules(self):
        with self.db() as c:
            catalog = {row['id']: row for row in collection_rows(c)}
            rows = [dict(row) for row in c.execute('SELECT * FROM classification_rules ORDER BY priority DESC,created_at,id')]
        for row in rows:
            row['conditions'], row['tags'] = json.loads(row['conditions']), json.loads(row['tags'])
            row['enabled'] = bool(row['enabled'])
            row['collection_path'] = catalog.get(row['collection_id'], {}).get('path')
            row['target_missing'] = bool(row['collection_id'] and row['collection_id'] not in catalog)
        return rows

    def save_classification_rule(self, name, conditions, collection_id=None, tags=None, priority=0, enabled=True, id=None, expected_version=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 80: raise ValueError('规则名称须为 1–80 字')
        conditions, tags = validate_conditions(conditions), tags_list(tags or [])
        if not collection_id and not tags: raise ValueError('规则至少添加一个分类或标签')
        if collection_id: identifier(collection_id)
        if type(priority) is not int or not -1000 <= priority <= 1000 or type(enabled) is not bool: raise ValueError('优先级或启用状态无效')
        if id: identifier(id)
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if collection_id and not c.execute('SELECT 1 FROM collections WHERE id=?', (collection_id,)).fetchone(): raise ValueError('目标分类不存在')
            prior = c.execute('SELECT * FROM classification_rules WHERE id=?', (id,)).fetchone() if id else None
            if id and (not prior or type(expected_version) is not int or prior['version'] != expected_version): raise ValueError('规则版本已变化，请重新加载')
            if not prior and c.execute('SELECT count(*) FROM classification_rules').fetchone()[0] >= 100: raise ValueError('最多保存 100 条规则')
            id, now = id or uuid.uuid4().hex, stamp()
            c.execute('INSERT OR REPLACE INTO classification_rules VALUES(?,?,?,?,?,?,?,?,?,?)', (id, name.strip(), prior['version'] + 1 if prior else 1, encode(conditions), collection_id, encode(tags), priority, int(enabled), prior['created_at'] if prior else now, now))
        return next(r for r in self.classification_rules() if r['id'] == id)

    def delete_classification_rule(self, id, expected_version):
        identifier(id)
        with self.lock, self.db() as c:
            changed = c.execute('DELETE FROM classification_rules WHERE id=? AND version=?', (id, expected_version)).rowcount
            if not changed: raise ValueError('规则版本已变化，请重新加载')
        return {'deleted': id}

    def preview_classification(self, scan_id, selected):
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rules = [dict(r) for r in c.execute('SELECT * FROM classification_rules WHERE enabled=1 ORDER BY priority DESC,created_at,id')]
            catalog = {r['id']: r for r in collection_rows(c)}
            items, targets = {}, {}
            for item in selected:
                suggestion = {'id': item['id'], 'path': item['path'], 'name': item['name'], 'collection_ids': [], 'tags': [], 'rules': [], 'conflicts': []}
                for rule in rules:
                    conditions = json.loads(rule['conditions'])
                    if not matches(item, conditions): continue
                    suggestion['rules'].append({'id': rule['id'], 'version': rule['version'], 'name': rule['name'], 'conditions': conditions})
                    if rule['collection_id']:
                        cid = rule['collection_id']
                        if cid not in catalog: suggestion['conflicts'].append('目标分类已删除：' + rule['name'])
                        else:
                            suggestion['collection_ids'].append(cid)
                            targets[cid] = catalog[cid]['path']
                    suggestion['tags'].extend(json.loads(rule['tags']))
                suggestion['collection_ids'] = list(dict.fromkeys(suggestion['collection_ids']))
                suggestion['tags'] = list(dict.fromkeys(suggestion['tags']))
                if len(suggestion['tags']) > 50: suggestion['conflicts'].append('合并后的标签超过 50 个')
                for prefix in ('载体:', '状态:', '版本:'):
                    if sum(t.startswith(prefix) for t in suggestion['tags']) > 1: suggestion['conflicts'].append(prefix + '命中多个不同值，请调整规则')
                suggestion['collections'] = [{'id': cid, 'path': targets[cid]} for cid in suggestion['collection_ids']]
                items[item['path']] = suggestion
            payload = {'scan_id': scan_id, 'items': items, 'guards': {item['path']: item['guard'] for item in selected},
                       'targets': targets, 'rule_versions': {r['id']: r['version'] for r in rules}}
            plan_id = uuid.uuid4().hex
            c.execute('INSERT INTO classification_plans(id,payload,created_at) VALUES(?,?,?)', (plan_id, encode(payload), stamp()))
        return {'plan_id': plan_id, 'items': list(items.values()), 'can_apply': not any(x['conflicts'] for x in items.values()),
                'matched': sum(bool(x['rules']) for x in items.values()), 'selected': len(items), 'notice': '仅追加分类和标签；不移动原件。无规则命中的文件仍按原方式入库。'}

    def classification_plan(self, c, plan_id, paths, guards, mode):
        identifier(plan_id)
        row = c.execute('SELECT * FROM classification_plans WHERE id=?', (plan_id,)).fetchone()
        if not row: raise ValueError('预览不存在，请重新预览')
        payload = json.loads(row['payload'])
        if set(payload['items']) != set(paths) or payload['guards'] != guards: raise ValueError('文件选择已变化，请重新预览')
        if row['job_id']:
            if row['mode'] != mode or not c.execute('SELECT 1 FROM jobs WHERE id=?', (row['job_id'],)).fetchone(): raise ValueError('预览已提交，请查看原任务或重新预览')
            return payload, row['job_id']
        current = dict(c.execute('SELECT id,version FROM classification_rules WHERE enabled=1'))
        if current != payload['rule_versions']: raise ValueError('规则已变化，请重新预览')
        catalog = {r['id']: r['path'] for r in collection_rows(c)}
        if any(catalog.get(cid) != path for cid, path in payload['targets'].items()): raise ValueError('分类已变化，请重新预览')
        if any(item['conflicts'] for item in payload['items'].values()): raise ValueError('分类建议有冲突，请先调整规则')
        return payload, None

    @staticmethod
    def _classification_metadata(c, fid):
        row = c.execute('SELECT f.sha,f.tags,s.mode FROM files f JOIN sources s ON s.id=f.source_id WHERE f.id=?', (fid,)).fetchone()
        if not row: raise ValueError('文件已移除')
        return row, {'tags': json.loads(row['tags']), 'collections': sorted(r[0] for r in c.execute('SELECT collection_id FROM collection_files WHERE file_id=?', (fid,)))}

    def apply_classification(self, plan_id, path, fid):
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            plan = c.execute('SELECT payload FROM classification_plans WHERE id=?', (plan_id,)).fetchone()
            if not plan: raise ValueError('分类预览记录不存在')
            item = json.loads(plan[0])['items'][path]
            row, before = self._classification_metadata(c, fid)
            previous = c.execute('SELECT * FROM classification_changes WHERE batch_id=? AND file_id=?', (plan_id, fid)).fetchone()
            if previous:
                if previous['undone_at'] or previous['sha'] != row['sha'] or json.loads(previous['after_json']) != before: raise ValueError('本批归类后资料已变化或已撤销，请重新预览')
                return {'verified': True, 'reused': True, 'batch_id': plan_id, **before}
            if row['mode'] == 'read-only' and item['tags']: raise ValueError('只读资料不能修改标签')
            after = {'tags': list(dict.fromkeys(before['tags'] + item['tags'])), 'collections': sorted(set(before['collections'] + item['collection_ids']))}
            if len(after['tags']) > 50: raise ValueError('合并已有标签后超过 50 个')
            for cid in item['collection_ids']:
                if not c.execute('SELECT 1 FROM collections WHERE id=?', (cid,)).fetchone(): raise ValueError('目标分类已删除，请重新预览')
                c.execute('INSERT OR IGNORE INTO collection_files VALUES(?,?,?)', (cid, fid, stamp()))
            if after['tags'] != before['tags']:
                c.execute('UPDATE files SET tags=?,updated_at=? WHERE id=?', (encode(after['tags']), stamp(), fid))
                self._index(c, fid)
            c.execute('INSERT INTO classification_changes VALUES(?,?,?,?,?,NULL)', (plan_id, fid, row['sha'], encode(before), encode(after)))
        self.sidecar(row['sha'])  # The import job writes the library manifest once after the batch.
        return {'verified': True, 'batch_id': plan_id, **after, 'added_collections': sorted(set(after['collections']) - set(before['collections'])), 'added_tags': [t for t in after['tags'] if t not in before['tags']]}

    def classification_batches(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('''SELECT p.id,p.created_at,p.job_id,count(x.file_id) AS files,
                coalesce(sum(x.undone_at IS NOT NULL),0) AS undone FROM classification_plans p
                LEFT JOIN classification_changes x ON x.batch_id=p.id WHERE p.job_id IS NOT NULL
                GROUP BY p.id ORDER BY p.created_at DESC LIMIT 30''')]

    def classification_batch(self, batch_id):
        identifier(batch_id)
        with self.db() as c:
            plan = c.execute('SELECT * FROM classification_plans WHERE id=?', (batch_id,)).fetchone()
            if not plan: raise ValueError('批次不存在')
            payload = json.loads(plan['payload'])
            rows = c.execute('''SELECT x.*,f.name FROM classification_changes x
                LEFT JOIN files f ON f.id=x.file_id WHERE x.batch_id=? ORDER BY f.name,x.file_id''', (batch_id,)).fetchall()
            files = []
            for row in rows:
                before, after = json.loads(row['before_json']), json.loads(row['after_json'])
                try:
                    current_row, current = self._classification_metadata(c, row['file_id'])
                    unchanged = current_row['sha'] == row['sha'] and current == after
                except ValueError: unchanged = False
                files.append({'file_id': row['file_id'], 'name': row['name'], 'sha256': row['sha'],
                              'before': before, 'after': after, 'unchanged_since_apply': unchanged,
                              'undone_at': row['undone_at'], 'added_tags': [t for t in after['tags'] if t not in before['tags']],
                              'added_collections': [{'id': cid, 'path_at_preview': payload['targets'][cid]} for cid in after['collections'] if cid not in before['collections']]})
        return {'batch_id': batch_id, 'job_id': plan['job_id'], 'created_at': plan['created_at'],
                'rule_versions': payload['rule_versions'], 'files': files}

    def undo_classification(self, batch_id):
        identifier(batch_id)
        changed, conflicts = [], []
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            plan = c.execute('SELECT job_id FROM classification_plans WHERE id=?', (batch_id,)).fetchone()
            if not plan: raise ValueError('批次不存在')
            job = c.execute('SELECT state FROM jobs WHERE id=?', (plan[0],)).fetchone()
            if job and job[0] in ('queued', 'running'): raise ValueError('请等待入库任务结束后再撤销')
            for item in c.execute('SELECT * FROM classification_changes WHERE batch_id=? AND undone_at IS NULL', (batch_id,)).fetchall():
                fid = item['file_id']
                try: row, current = self._classification_metadata(c, fid)
                except ValueError:
                    conflicts.append({'file_id': fid, 'reason': '文件已移除'}); continue
                before, after = json.loads(item['before_json']), json.loads(item['after_json'])
                if row['sha'] != item['sha'] or current != after:
                    conflicts.append({'file_id': fid, 'reason': '内容或归属已变化，保留现状'}); continue
                for cid in set(after['collections']) - set(before['collections']):
                    c.execute('DELETE FROM collection_files WHERE collection_id=? AND file_id=?', (cid, fid))
                if before['tags'] != after['tags']:
                    c.execute('UPDATE files SET tags=?,updated_at=? WHERE id=?', (encode(before['tags']), stamp(), fid))
                    self._index(c, fid)
                c.execute('UPDATE classification_changes SET undone_at=? WHERE batch_id=? AND file_id=?', (stamp(), batch_id, fid))
                changed.append(fid)
        if changed: self._sync_collection_metadata(changed)
        return {'batch_id': batch_id, 'undone': changed, 'conflicts': conflicts, 'notice': '只撤销本批新增的分类和标签，文件保留。'}
