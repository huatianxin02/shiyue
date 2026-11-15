"""Candidate filters and scope-safe result metadata, shared by HTTP and MCP."""
import json
import re
from collection_tree import collection_rows, descendant_query
from knowledge_index import scope_sql

CAPABILITIES = {
    'collection_tree': 1, 'collection_move': 1, 'classify': 1,
    'knowledge_filters': 1, 'knowledge_metadata': 1,
    'file_preferences': 1, 'classification_rules': 1,
}
FILTER_SCHEMA = {
    'collection_ids': {'type': 'array', 'maxItems': 50, 'items': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'}},
    'collection_paths': {'type': 'array', 'maxItems': 50, 'items': {'type': 'array', 'minItems': 1, 'maxItems': 9, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 40}}},
    'tags': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 80}},
    'include_children': {'type': 'boolean', 'default': True},
    'include_paths': {'type': 'boolean', 'default': False},
}


def http_filters(query):
    result = {}
    for key in ('collection_ids', 'collection_paths', 'tags'):
        if key in query:
            result[key] = json.loads(query[key])
    for key in ('include_children', 'include_paths'):
        if key in query:
            if query[key] not in ('true', 'false', '1', '0'):
                raise ValueError(key + ' 必须为 true 或 false')
            result[key] = query[key] in ('true', '1')
    return result


def filter_sql(c, collection_ids=None, collection_paths=None, tags=None, include_children=True, include_paths=False):
    if type(include_children) is not bool or type(include_paths) is not bool:
        raise ValueError('include_children/include_paths 必须为布尔值')
    values = [collection_ids, collection_paths, tags]
    for value, limit in zip(values, (50, 50, 20)):
        if value is not None and (not isinstance(value, list) or len(value) > limit):
            raise ValueError('筛选参数必须为限定长度的数组')
    ids = list(collection_ids or [])
    if any(not isinstance(x, str) or not re.fullmatch(r'[0-9a-f]{32}', x) for x in ids):
        raise ValueError('分类 ID 无效')
    paths, tags = collection_paths or [], tags or []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip() or len(tag) > 80 or any(ord(ch) < 32 for ch in tag):
            raise ValueError('标签须为 1–80 字的完整名称')
    hierarchical = 'parent_id' in {r[1] for r in c.execute('PRAGMA table_info(collections)')}
    for path in paths:
        if not isinstance(path, list) or not 1 <= len(path) <= 9:
            raise ValueError('分类路径须为 1–9 级名称数组')
        parent, found = None, True
        for name in path:
            if not isinstance(name, str) or not name.strip() or len(name) > 40 or any(ord(ch) < 32 for ch in name):
                raise ValueError('分类名称无效')
            if not found:
                continue
            if hierarchical:
                row = c.execute('SELECT id FROM collections WHERE parent_id IS ? AND name=? COLLATE NOCASE', (parent, ' '.join(name.split()))).fetchone()
            else:
                row = c.execute('SELECT id FROM collections WHERE name=? COLLATE NOCASE', (name,)).fetchone() if parent is None else None
            found = bool(row)
            if row:
                parent = row[0]
        if found:
            ids.append(parent)
    ids = list(dict.fromkeys(ids))
    clauses, args = [], []
    if collection_ids or paths:
        if not ids:
            clauses.append('0')
        else:
            placeholders = ','.join('?' for _ in ids)
            selected = descendant_query(placeholders) if include_children and hierarchical else 'SELECT id FROM collections WHERE id IN (' + placeholders + ')'
            clauses.append('EXISTS(SELECT 1 FROM collection_files cf WHERE cf.file_id=f.id AND cf.collection_id IN (' + selected + '))')
            args.extend(ids)
    for tag in dict.fromkeys(tags):
        clauses.append('EXISTS(SELECT 1 FROM json_each(f.tags) WHERE value=?)')
        args.append(tag)
    return ' AND '.join(clauses) or '1', args, {
        'collection_ids': ids, 'collection_paths': paths, 'tags': list(dict.fromkeys(tags)),
        'include_children': include_children, 'collection_operator': 'OR', 'tag_operator': 'AND',
    }


def enrich(c, results, profile, include_paths=False):
    """One snapshot, bounded joins; never disclose a preferred file outside scope."""
    if not results:
        return
    ids = [r['id'] for r in results]
    placeholders = ','.join('?' for _ in ids)
    catalog = {r['id']: {k: r[k] for k in ('id', 'name', 'parent_id', 'path', 'path_label', 'depth')} for r in collection_rows(c)}
    memberships = {fid: [] for fid in ids}
    for row in c.execute('SELECT file_id,collection_id FROM collection_files WHERE file_id IN (' + placeholders + ')', ids):
        if row[1] in catalog:
            memberships[row[0]].append(catalog[row[1]])
    origins = dict(c.execute('SELECT id,origin_path FROM files WHERE id IN (' + placeholders + ')', ids)) if include_paths else {}
    preferences = {}
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='file_preferences'").fetchone():
        scope, args = scope_sql(c, profile)
        rows = c.execute('''SELECT p.*,f.name FROM file_preferences p JOIN files f ON f.id=p.preferred_id
            JOIN files s ON s.id=p.file_id WHERE p.file_id IN (''' + placeholders + ''')
            AND s.sha=p.source_sha AND f.sha=p.target_sha AND ''' + scope, ids + args)
        preferences = {r['file_id']: {'id': r['preferred_id'], 'title': r['name'], 'sha256': r['target_sha'],
                       'reason': r['reason'], 'specified_by': 'owner', 'updated_at': r['updated_at']} for r in rows}
    for result in results:
        result['collections'] = sorted(memberships[result['id']], key=lambda x: (x['path_label'], x['id']))
        if include_paths:
            result['origin_path'] = origins.get(result['id'])
        if result['id'] in preferences:
            result['preferred_file'] = preferences[result['id']]
