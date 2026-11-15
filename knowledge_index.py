"""Versioned evidence and explicit AI scopes, stored alongside the original index.

SQLite is the authority. FTS rows are rebuildable; extraction text is immutable.
No third-party model or network access is required.
"""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone

from chinese_search import index_words

SPLITTER_VERSION = 1


def stamp():
    return datetime.now(timezone.utc).isoformat()


def tokens(text):
    return ' '.join(re.findall(r'[\u3400-\u9fff]|[^\W_]+', re.sub(r'([\u3400-\u9fff])', r' \1 ', text.lower()), re.UNICODE))


def extraction_hash(chunks):
    return hashlib.sha256(json.dumps(chunks, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def split_passages(chunks):
    """Split at paragraph/sentence boundaries; keep offsets into legacy chunks."""
    for index, chunk in enumerate(chunks):
        text = chunk.get('text', '')
        heading = chunk.get('label', '')
        for block in re.finditer(r'\S(?:.*?\S)?(?=\n\s*\n|\Z)', text, re.S):
            position, end = block.span()
            while position < end:
                stop = min(position + 800, end)
                if stop < end:
                    boundary = list(re.finditer(r'[。！？.!?；;]\s*|\n', text[position + 350:stop]))
                    if boundary:
                        stop = position + 350 + boundary[-1].end()
                raw = text[position:stop]
                leading = len(raw) - len(raw.lstrip())
                body = raw.strip()
                if body:
                    if body.startswith('#'):
                        heading = body.splitlines()[0].lstrip('# ').strip()[:200]
                    yield {'text': body, 'legacy_index': index, 'chunk_offset': position + leading,
                           'offset': int(chunk.get('offset') or 0) + position + leading,
                           'page': chunk.get('page'), 'bounds': chunk.get('bounds'),
                           'label': chunk.get('label', ''), 'heading': heading}
                position = stop


SCHEMA = '''
CREATE TABLE IF NOT EXISTS extractions(
 id TEXT PRIMARY KEY, object_sha TEXT NOT NULL REFERENCES objects(sha) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, splitter_version INTEGER NOT NULL, parser_version TEXT NOT NULL,
 chunks_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(object_sha,fingerprint,splitter_version));
CREATE TABLE IF NOT EXISTS active_extractions(
 object_sha TEXT PRIMARY KEY REFERENCES objects(sha) ON DELETE CASCADE,
 extraction_id TEXT NOT NULL REFERENCES extractions(id));
CREATE TABLE IF NOT EXISTS passages(
 id TEXT PRIMARY KEY, extraction_id TEXT NOT NULL REFERENCES extractions(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, legacy_index INTEGER NOT NULL, chunk_offset INTEGER NOT NULL,
 text TEXT NOT NULL, page INTEGER, bounds TEXT, label TEXT, heading TEXT, offset INTEGER,
 UNIQUE(extraction_id,ordinal));
CREATE INDEX IF NOT EXISTS passages_extraction ON passages(extraction_id,ordinal);
CREATE INDEX IF NOT EXISTS extractions_object ON extractions(object_sha,fingerprint);
CREATE VIRTUAL TABLE IF NOT EXISTS passage_search USING fts5(passage_id UNINDEXED,text,heading,tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS passage_terms USING fts5(passage_id UNINDEXED,text,tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS passages_delete AFTER DELETE ON passages BEGIN
 DELETE FROM passage_search WHERE passage_id=old.id;
 DELETE FROM passage_terms WHERE passage_id=old.id;
END;
CREATE TABLE IF NOT EXISTS knowledge_profiles(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL,
 all_files INTEGER NOT NULL, source_ids TEXT NOT NULL, collection_ids TEXT NOT NULL,
 updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS file_notes(
 file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
 body TEXT NOT NULL, updated_at TEXT NOT NULL);
'''


def record_extraction(c, sha, chunks, parser_version='builtin-2'):
    if any(chunk.get('ocr') for chunk in chunks) and '+vision-ocr' not in parser_version:
        parser_version += '+vision-ocr-1'
    fingerprint = extraction_hash(chunks)
    extraction_id = hashlib.sha256(f'{sha}:{fingerprint}:{SPLITTER_VERSION}'.encode()).hexdigest()
    exists = c.execute('SELECT 1 FROM extractions WHERE id=?', (extraction_id,)).fetchone()
    if not exists:
        c.execute('INSERT INTO extractions VALUES(?,?,?,?,?,?,?)',
                  (extraction_id, sha, fingerprint, SPLITTER_VERSION, parser_version,
                   json.dumps(chunks, ensure_ascii=False), stamp()))
        for ordinal, p in enumerate(split_passages(chunks)):
            pid = hashlib.sha256(f'{extraction_id}:{ordinal}'.encode()).hexdigest()
            c.execute('INSERT INTO passages VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (pid, extraction_id, ordinal, p['legacy_index'], p['chunk_offset'], p['text'],
                       p['page'], json.dumps(p['bounds']), p['label'], p['heading'], p['offset']))
            c.execute('INSERT INTO passage_search VALUES(?,?,?)', (pid, tokens(p['text']), tokens(p['heading'])))
            c.execute('INSERT INTO passage_terms VALUES(?,?)', (pid,index_words(p['text'])))
    c.execute('INSERT INTO active_extractions VALUES(?,?) ON CONFLICT(object_sha) DO UPDATE SET extraction_id=excluded.extraction_id',
              (sha, extraction_id))
    return extraction_id


def initialize(vault):
    with vault.db() as c:
        ready = c.execute("SELECT value FROM settings WHERE key='knowledge_schema'").fetchone()
    if not ready:
        # The snapshot belongs to the old schema, before any new tables or backfill.
        backup = vault.root / 'backups' / ('pre-1.4.0-' + uuid.uuid4().hex[:8] + '.sqlite3')
        with closing(sqlite3.connect(vault.dbpath)) as src, closing(sqlite3.connect(backup)) as dst:
            src.backup(dst)
    with vault.db() as c:
        c.executescript(SCHEMA)
        c.execute('BEGIN IMMEDIATE')
        if not ready:
            for row in c.execute('SELECT sha,chunks_json,extract_version FROM objects').fetchall():
                record_extraction(c, row['sha'], json.loads(row['chunks_json']), f'builtin-{row["extract_version"]}')
            c.execute("INSERT OR IGNORE INTO knowledge_profiles VALUES('default','默认 AI 访问',1,1,'[]','[]',?)", (stamp(),))
            c.execute("INSERT OR REPLACE INTO settings VALUES('knowledge_schema','1')")


def scope_sql(c, profile='default', alias='f'):
    """Evaluate on every call, before ranking/LIMIT. None is local owner UI only."""
    if profile is None:
        return '1', []
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge_profiles'").fetchone():
        if profile == 'default':
            return '1', []
        raise ValueError('AI 访问范围不存在或已撤销')
    row = c.execute('SELECT * FROM knowledge_profiles WHERE id=?', (profile,)).fetchone()
    if not row or not row['enabled']:
        raise ValueError('AI 访问范围不存在或已撤销')
    if row['all_files']:
        return '1', []
    sources, collections = json.loads(row['source_ids']), json.loads(row['collection_ids'])
    if not sources and not collections:
        return '0', []
    clauses, args = [], []
    if sources:
        clauses.append(f'{alias}.source_id IN (' + ','.join('?' for _ in sources) + ')')
        args.extend(sources)
    if collections:
        placeholders=','.join('?' for _ in collections)
        if 'parent_id' in {row[1] for row in c.execute('PRAGMA table_info(collections)')}:
            from collection_tree import descendant_query
            selected=descendant_query(placeholders)
        else:
            selected='SELECT id FROM collections WHERE id IN ('+placeholders+')'
        clauses.append(f'EXISTS(SELECT 1 FROM collection_files cf WHERE cf.file_id={alias}.id AND cf.collection_id IN ('+selected+'))')
        args.extend(collections)
    return ' AND '.join(clauses), args


class KnowledgeFeatures:
    def knowledge_health(self):
        with self.db() as c:
            passages = c.execute('SELECT count(*) FROM passages').fetchone()[0]
            indexed = c.execute('SELECT count(*) FROM passage_search').fetchone()[0]
            terms = c.execute('SELECT count(*) FROM passage_terms').fetchone()[0]
            missing = c.execute('SELECT count(*) FROM objects o LEFT JOIN active_extractions a ON a.object_sha=o.sha WHERE a.extraction_id IS NULL').fetchone()[0]
            return {'passages': passages, 'extractions': c.execute('SELECT count(*) FROM extractions').fetchone()[0],
                    'notes': c.execute('SELECT count(*) FROM file_notes WHERE body!=""').fetchone()[0],
                    'indexed_passages': indexed, 'word_indexed_passages': terms,
                    'missing_extractions': missing, 'counts_consistent': passages == indexed == terms and missing == 0,
                    'notice': '此处检查计数一致性；重建检索索引会从已保留正文重新生成索引。'}

    def job_rebuild_knowledge(self, jid, payload):
        # One transaction: cancellation or failure leaves the previous index intact.
        with self.lock, self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for obj in c.execute('SELECT sha,chunks_json FROM objects').fetchall():
                self.checkpoint()
                record_extraction(c, obj['sha'], json.loads(obj['chunks_json']))
            c.execute('DELETE FROM passage_search')
            c.execute('DELETE FROM passage_terms')
            count = 0
            for p in c.execute('SELECT * FROM passages').fetchall():
                self.checkpoint()
                c.execute('INSERT INTO passage_search VALUES(?,?,?)', (p['id'], tokens(p['text']), tokens(p['heading'] or '')))
                c.execute('INSERT INTO passage_terms VALUES(?,?)', (p['id'], index_words(p['text'])))
                count += 1
            for f in c.execute('SELECT id FROM files').fetchall():
                self._index(c, f['id'])
            for table in ('search', 'passage_search', 'passage_terms'):
                c.execute(f"INSERT INTO {table}({table}) VALUES('integrity-check')")
        return {'passages': count, 'errors': []}

    def knowledge_profiles(self):
        with self.db() as c:
            out = []
            for r in c.execute('SELECT * FROM knowledge_profiles ORDER BY updated_at,id'):
                row = dict(r)
                for key in ('source_ids', 'collection_ids'):
                    row[key] = json.loads(row[key])
                out.append(row)
            return out

    def save_knowledge_profile(self, name, source_ids=None, collection_ids=None, all_files=False, enabled=True, id=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('范围名称需要 1–80 字')
        if type(all_files) is not bool or type(enabled) is not bool:
            raise ValueError('范围开关无效')
        with self.db() as c:
            for values, table in ((source_ids, 'sources'), (collection_ids, 'collections')):
                if values is not None and (not isinstance(values, list) or len(values) > 1000 or any(not isinstance(v, str) for v in values)):
                    raise ValueError('范围选择无效')
                if any(not c.execute(f'SELECT 1 FROM {table} WHERE id=?', (v,)).fetchone() for v in values or []):
                    raise ValueError('所选来源或分类已不存在')
            if id is not None and not c.execute('SELECT 1 FROM knowledge_profiles WHERE id=?', (id,)).fetchone():
                raise ValueError('AI 访问范围不存在')
            identifier = id or uuid.uuid4().hex
            c.execute('INSERT INTO knowledge_profiles VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,all_files=excluded.all_files,source_ids=excluded.source_ids,collection_ids=excluded.collection_ids,updated_at=excluded.updated_at',
                      (identifier, name.strip(), int(enabled), int(all_files), json.dumps(source_ids or []), json.dumps(collection_ids or []), stamp()))
        return {'id': identifier}

    def note(self, file_id, body=None, expected_updated_at=None):
        with self.db() as c:
            if not c.execute('SELECT 1 FROM files WHERE id=?', (file_id,)).fetchone():
                raise ValueError('资料不存在')
            row = c.execute('SELECT * FROM file_notes WHERE file_id=?', (file_id,)).fetchone()
            if body is not None:
                if not isinstance(body, str) or len(body) > 100000:
                    raise ValueError('笔记最多 10 万字')
                if (row['updated_at'] if row else '') != (expected_updated_at or ''):
                    raise ValueError('笔记已在其他窗口修改，请重新打开后合并')
                c.execute('INSERT INTO file_notes VALUES(?,?,?) ON CONFLICT(file_id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at', (file_id, body, stamp()))
                row = c.execute('SELECT * FROM file_notes WHERE file_id=?', (file_id,)).fetchone()
            return dict(row) if row else {'file_id': file_id, 'body': '', 'updated_at': ''}
