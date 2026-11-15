"""Regression tests for the timeline sort key, derivative caching and parallel ingest."""
import datetime, json, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import Vault, now
from media import capture_epoch
import test_server


class EpochTests(unittest.TestCase):
    def test_offsets_resolve_to_the_same_absolute_moment(self):
        self.assertEqual(capture_epoch('2024-06-18T15:30:00+08:00'), capture_epoch('2024-06-18T07:30:00Z'))
        self.assertEqual(capture_epoch('2024-06-18T15:30:00+08:00'), capture_epoch('2024-06-18T02:30:00-05:00'))

    def test_naive_stamp_keeps_the_local_wall_clock(self):
        local = datetime.datetime.now().astimezone().tzinfo
        expected = datetime.datetime(2024, 6, 18, 15, 30, tzinfo=local).timestamp()
        self.assertAlmostEqual(capture_epoch('2024-06-18T15:30:00'), expected, places=0)

    def test_unusable_values_are_ignored(self):
        for value in (None, '', 'not-a-date', '0000:00:00 00:00:00', 12345):
            self.assertIsNone(capture_epoch(value), value)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.inputs = self.base / 'inputs'; self.inputs.mkdir()
        self.v = Vault(self.base / 'vault', start_worker=False)
        self.sid = self.v.add_source(self.inputs, 'in-place')

    def tearDown(self): self.v.close(); self.temp.cleanup()

    def seed(self, name, sha, captured_at, ts):
        stamp = now()
        with self.v.db() as c:
            c.execute("INSERT INTO objects(sha,size,mime,created_at,chunks_json,warning,verified_at,integrity,extract_version) VALUES(?,?,?,?,?,?,?,?,?)",
                      (sha, 1, 'image/png', stamp, '[]', '', stamp, 'ok', 2))
            c.execute("INSERT INTO files(id,sha,source_id,name,origin_path,kind,added_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      (sha[:8], sha, self.sid, name, str(self.inputs / name), 'image', stamp, stamp))
            c.execute("INSERT INTO media(sha,captured_at,captured_ts,version) VALUES(?,?,?,?)", (sha, captured_at, ts, 2))

    def test_sort_follows_absolute_time_not_the_wall_clock(self):
        # 23:00 in Beijing is 15:00Z; 16:00Z is later but reads earlier on a wall clock.
        self.seed('beijing.png', 'a' * 64, '2024-06-18T23:00:00+08:00', capture_epoch('2024-06-18T23:00:00+08:00'))
        self.seed('utc.png', 'b' * 64, '2024-06-18T16:00:00+00:00', capture_epoch('2024-06-18T16:00:00+00:00'))
        names = [f['name'] for f in self.v.list_files(sort='captured')['files']]
        self.assertEqual(names, ['utc.png', 'beijing.png'])

    def test_undated_media_sinks_to_the_bottom(self):
        self.seed('dated.png', 'c' * 64, '2024-06-18T10:00:00+08:00', capture_epoch('2024-06-18T10:00:00+08:00'))
        self.seed('undated.png', 'd' * 64, None, None)
        self.assertEqual([f['name'] for f in self.v.list_files(sort='captured')['files']], ['dated.png', 'undated.png'])


class DerivativeCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.inputs = self.base / 'inputs'; self.inputs.mkdir()
        self.v = Vault(self.base / 'vault', start_worker=False)
        sid = self.v.add_source(self.inputs, 'in-place'); stamp = now(); sha = 'e' * 64
        (self.v.root / 'thumbs' / 'ee').mkdir(parents=True, exist_ok=True)
        (self.v.root / 'thumbs' / 'ee' / (sha + '.jpg')).write_bytes(b'\xff\xd8\xff\xd9')
        with self.v.db() as c:
            c.execute("INSERT INTO objects(sha,size,mime,created_at,chunks_json,warning,verified_at,integrity,extract_version) VALUES(?,?,?,?,?,?,?,?,?)",
                      (sha, 1, 'image/jpeg', stamp, '[]', '', stamp, 'ok', 2))
            c.execute("INSERT INTO files(id,sha,source_id,name,origin_path,kind,added_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                      ('thumbfile', sha, sid, 'photo.heic', str(self.inputs / 'photo.heic'), 'image', stamp, stamp))
            c.execute("INSERT INTO media(sha,thumb_path,version) VALUES(?,?,?)", (sha, 'thumbs/ee/' + sha + '.jpg', 2))
        self.server = SimpleNamespace(server_port=12345, origin='http://127.0.0.1:12345', token='test-token', vault=self.v)

    def tearDown(self): self.v.close(); self.temp.cleanup()

    def request(self, path):
        sock = test_server.FakeSocket(('GET ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1:12345\r\nX-Vault-Token: test-token\r\nConnection: close\r\n\r\n').encode())
        test_server.Handler(sock, ('127.0.0.1', 999), self.server)
        head, body = sock.outgoing.getvalue().split(b'\r\n\r\n', 1)
        return int(head.split()[1]), head.decode(), body

    def test_thumbnail_is_immutable_so_a_photo_grid_can_reuse_it(self):
        code, headers, body = self.request('/api/files/thumbfile/thumbnail')
        self.assertEqual(code, 200); self.assertEqual(body, b'\xff\xd8\xff\xd9')
        cache = [line for line in headers.split('\r\n') if line.lower().startswith('cache-control')]
        self.assertEqual(len(cache), 1, cache)
        self.assertIn('immutable', cache[0])

    def test_json_responses_stay_uncacheable(self):
        code, headers, _ = self.request('/api/files')
        self.assertEqual(code, 200)
        self.assertIn('no-store', headers)


class ParallelImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.inputs = self.base / 'inputs'; self.inputs.mkdir()
        self.v = Vault(self.base / 'vault', start_worker=False)

    def tearDown(self): self.v.close(); self.temp.cleanup()

    def run_import(self):
        submitted = self.v.submit_import([str(self.inputs)], 'in-place')
        with self.v.db() as c:
            job = dict(c.execute('SELECT * FROM jobs WHERE id=?', (submitted['job_id'],)).fetchone())
        self.v.run_job(job)
        with self.v.db() as c:
            row = dict(c.execute('SELECT * FROM jobs WHERE id=?', (submitted['job_id'],)).fetchone())
        return json.loads(row['result']) if row['result'] else {}, row

    def test_every_file_is_indexed_once_under_concurrency(self):
        for i in range(24):
            (self.inputs / f'note{i:02d}.md').write_text(f'第 {i} 份会议记录，讨论服务器租赁费用。')
        result, row = self.run_import()
        self.assertEqual(row['state'], 'done', row['error'])
        self.assertEqual(result['imported'], 24)
        with self.v.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM files').fetchone()[0], 24)
            self.assertEqual(c.execute('SELECT count(DISTINCT origin_path) FROM files').fetchone()[0], 24)
            self.assertEqual(c.execute('SELECT count(*) FROM search').fetchone()[0], 24)

    def test_duplicates_still_collapse_to_one_object(self):
        for i in range(8):
            (self.inputs / f'copy{i}.txt').write_text('完全相同的内容，用于验证并发去重。')
        self.run_import()
        self.assertEqual(self.v.status()['stats']['objects'], 1)
        self.assertEqual(self.v.status()['stats']['files'], 8)

    def test_search_is_complete_after_parallel_ingest(self):
        for i in range(12):
            (self.inputs / f'memo{i}.md').write_text(f'项目 {i} 的预算审批记录。')
        self.run_import()
        self.assertEqual(self.v.list_files(q='预算审批')['total'], 12)


class MigrationTests(unittest.TestCase):
    """An existing library must open even though the sort key column is new."""

    def test_older_library_gains_the_sort_key_without_losing_rows(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'vault'; root.mkdir()
            sha = 'f' * 64; stamp = '2024-06-18T07:30:00+00:00'
            with closing(sqlite3.connect(root / 'library.sqlite3')) as c, c:
                c.executescript("""
                CREATE TABLE sources(id TEXT PRIMARY KEY,name TEXT,root TEXT,mode TEXT,kind TEXT,created_at TEXT,last_scan TEXT);
                CREATE TABLE objects(sha TEXT PRIMARY KEY,size INTEGER,mime TEXT,vault_path TEXT,created_at TEXT,
                  chunks_json TEXT,warning TEXT,verified_at TEXT,integrity TEXT DEFAULT 'unchecked');
                CREATE TABLE files(id TEXT PRIMARY KEY,sha TEXT,source_id TEXT,name TEXT,origin_path TEXT,kind TEXT,
                  tags TEXT DEFAULT '[]',starred INTEGER DEFAULT 0,added_at TEXT,updated_at TEXT,UNIQUE(source_id,origin_path));
                CREATE TABLE media(sha TEXT PRIMARY KEY,width INTEGER,height INTEGER,duration REAL,
                  captured_at TEXT,captured_source TEXT,camera TEXT,make TEXT,gps_lat REAL,gps_lon REAL,
                  codec TEXT,thumb_path TEXT,media_error TEXT DEFAULT '',version INTEGER DEFAULT 1);
                """)
                c.execute("INSERT INTO sources VALUES('s1','旧来源','/tmp','in-place','folder',?,NULL)", (stamp,))
                c.execute("INSERT INTO objects VALUES(?,?,?,?,?,?,?,?,'ok')", (sha, 1, 'image/jpeg', None, stamp, '[]', '', stamp))
                c.execute("INSERT INTO files VALUES('f1',?,'s1','old.jpg','/tmp/old.jpg','image','[]',0,?,?)", (sha, stamp, stamp))
                c.execute("INSERT INTO media(sha,captured_at,camera,version) VALUES(?,?,?,?)",
                          (sha, '2024-06-18T15:30:00+08:00', 'QA Phone', 1))
            v = Vault(root, start_worker=False)
            try:
                with v.db() as c:
                    self.assertIn('captured_ts', {r[1] for r in c.execute('PRAGMA table_info(media)')})
                    self.assertIn('extract_version', {r[1] for r in c.execute('PRAGMA table_info(objects)')})
                    row = c.execute("SELECT captured_at,camera,version FROM media WHERE sha=?", (sha,)).fetchone()
                    self.assertEqual(row[0], '2024-06-18T15:30:00+08:00')
                    self.assertEqual(row[1], 'QA Phone')
                    self.assertEqual(row[2], 1)
                    self.assertIn('media_ts', {r[1] for r in c.execute('PRAGMA index_list(media)')})
                    # The outdated media row schedules exactly one backfill job.
                    self.assertEqual(c.execute("SELECT count(*) FROM jobs WHERE kind='refresh'").fetchone()[0], 1)
            finally:
                v.close()


if __name__ == '__main__':
    unittest.main()
