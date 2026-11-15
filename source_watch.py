"""FSEvents are hints. A durable queue and periodic reconciliation are the truth."""
import json
import subprocess
import threading
import time
from pathlib import Path


SCHEMA = '''CREATE TABLE IF NOT EXISTS source_watch(
 source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
 enabled INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL DEFAULT 0,
 next_check REAL NOT NULL DEFAULT 0, last_check TEXT, last_error TEXT NOT NULL DEFAULT '',
 missing INTEGER NOT NULL DEFAULT 0);
'''


class WatchFeatures:
    def init_watch(self):
        with self.db() as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO source_watch(source_id) SELECT id FROM sources WHERE kind!='pack'")
            # Reconcile once after every restart, including sources that were offline.
            c.execute('UPDATE source_watch SET next_check=0')
        self._watch_process = None
        self._watch_roots = ()
        self._watch_retry = 0
        self._watch_tick = 0

    def set_watch(self, source_id, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动更新开关无效')
        with self.db() as c:
            if not c.execute("SELECT 1 FROM sources WHERE id=? AND kind!='pack'", (source_id,)).fetchone():
                raise ValueError('该来源不能自动更新')
            c.execute('INSERT INTO source_watch(source_id,enabled) VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET enabled=excluded.enabled,next_check=0', (source_id, int(enabled)))
            if not enabled:
                for job in c.execute("SELECT id,payload FROM jobs WHERE kind='import' AND state='queued'").fetchall():
                    p = json.loads(job['payload'])
                    if p.get('auto_source') == source_id:
                        c.execute('DELETE FROM jobs WHERE id=?', (job['id'],))
        self.wake.set()
        return {'enabled': enabled}

    def notify_sources(self, paths=None):
        """Coalesce bursts, persisting the generation even while an import is running."""
        with self.db() as c:
            for row in c.execute('SELECT s.*,w.enabled FROM sources s JOIN source_watch w ON w.source_id=s.id WHERE w.enabled=1').fetchall():
                root = Path(row['root'])
                affected = paths is None
                for raw in paths or []:
                    path = Path(raw)
                    if path == self.root or self.root in path.parents:
                        continue
                    if path == root or (row['kind'] == 'folder' and root in path.parents):
                        affected = True
                        break
                if affected:
                    c.execute('UPDATE source_watch SET generation=generation+1,next_check=? WHERE source_id=?', (time.time() + 3, row['id']))
        self.wake.set()

    def _watch_output(self, process):
        try:
            for line in process.stdout:
                if self.stop.is_set():
                    break
                event = json.loads(line)
                if event.get('ready'):
                    continue
                self.notify_sources(None if event.get('rescan') else event.get('paths', []))
        except (ValueError, OSError):
            pass  # The timer remains authoritative and will restart the helper.

    def stop_watch(self):
        process = self._watch_process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process and process.stdout:
            process.stdout.close()
        self._watch_process = None

    def watch_tick(self, force=False):
        clock = time.time()
        if not force and clock < self._watch_tick:
            return
        self._watch_tick = clock + 2
        with self.db() as c:
            c.execute("INSERT OR IGNORE INTO source_watch(source_id) SELECT id FROM sources WHERE kind!='pack'")
            sources = [dict(r) for r in c.execute('SELECT s.*,w.generation,w.next_check FROM sources s JOIN source_watch w ON s.id=w.source_id WHERE w.enabled=1')]
            pending = [json.loads(r[0]) for r in c.execute("SELECT payload FROM jobs WHERE kind='import' AND state IN ('queued','running')")]
        valid_sources=[]
        for source in sources:
            if source['kind']=='file':
                from disk_discovery import candidate_guard
                try:candidate_guard(source['root'])
                except ValueError as error:
                    with self.db() as c:
                        c.execute('UPDATE source_watch SET next_check=?,last_error=? WHERE source_id=?',
                                  (clock+60,'单文件来源已离线或不再是普通文件：'+str(error),source['id']))
                    continue
            valid_sources.append(source)
        sources=valid_sources
        roots = tuple(sorted({s['root'] if s['kind'] == 'folder' else str(Path(s['root']).parent) for s in sources if Path(s['root']).exists()}))
        helper = Path(__file__).parent / 'bin' / 'EventWatch'
        dead = self._watch_process is None or self._watch_process.poll() is not None
        if roots != self._watch_roots or (dead and clock >= self._watch_retry):
            self.stop_watch()
            self._watch_roots, self._watch_retry = roots, clock + 60
            if roots and helper.is_file():
                try:
                    process = subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                    process.stdin.write(json.dumps(list(roots)) + '\n')
                    process.stdin.close()
                    self._watch_process = process
                    threading.Thread(target=self._watch_output, args=(process,), daemon=True).start()
                except OSError:
                    pass
        for source in sources:
            if source['next_check'] > clock:
                continue
            if any(any(i.get('source_id') == source['id'] for i in p.get('items', [])) for p in pending):
                continue
            if not Path(source['root']).exists():
                with self.db() as c:
                    c.execute("UPDATE source_watch SET next_check=?,last_error='来源离线，等待重新连接' WHERE source_id=?", (clock + 60, source['id']))
                continue
            payload = {'items': [{'path': source['root'], 'source_id': source['id']}],
                       'mode': source['mode'], 'incremental': True,
                       'auto_source': source['id'], 'watch_generation': source['generation']}
            self.queue('import', payload)
            pending.append(payload)
            with self.db() as c:
                c.execute('UPDATE source_watch SET next_check=? WHERE source_id=?', (clock + 300, source['id']))

    def finish_watch(self, payload, error=''):
        sid = payload.get('auto_source')
        if not sid:
            return
        with self.db() as c:
            from datetime import datetime, timezone
            paths = [r[0] for r in c.execute('SELECT origin_path FROM files WHERE source_id=?', (sid,))]
            missing = sum(not Path(p).is_file() for p in paths)
            c.execute('UPDATE source_watch SET last_check=?,last_error=?,missing=?,next_check=CASE WHEN generation=? THEN ? ELSE next_check END WHERE source_id=?',
                      (datetime.now(timezone.utc).isoformat(), error[:1000], missing,
                       payload.get('watch_generation', -1), time.time() + 300, sid))

    def reconcile_moves(self, items):
        """Only relink a unique vanished path with a unique equal-content new path."""
        from vault import digest, now
        by_source = {}
        for path, sid in items:
            by_source.setdefault(sid, []).append(path)
        for sid, paths in by_source.items():
            with self.db() as c:
                known = [dict(r) for r in c.execute('SELECT id,sha,origin_path FROM files WHERE source_id=?', (sid,))]
            indexed = {r['origin_path'] for r in known}
            missing = {}
            for r in known:
                if not Path(r['origin_path']).exists():
                    missing.setdefault(r['sha'], []).append(r)
            if not missing:
                continue
            new = {}
            for path in paths:
                self.checkpoint()
                if str(path.resolve()) not in indexed:
                    try:
                        sha = digest(path)
                        if sha in missing:
                            new.setdefault(sha, []).append(path)
                    except OSError:
                        pass
            for sha, old in missing.items():
                if len(old) == 1 and len(new.get(sha, [])) == 1:
                    path = new[sha][0]
                    # ingest() will re-read and validate content before indexing.
                    with self.lock, self.db() as c:
                        if not Path(old[0]['origin_path']).exists():
                            c.execute('UPDATE files SET origin_path=?,name=?,updated_at=? WHERE id=? AND origin_path=?',
                                      (str(path.resolve()), path.name, now(), old[0]['id'], old[0]['origin_path']))
                            c.execute('DELETE FROM scan_state WHERE file_id=?', (old[0]['id'],))
