"""Real local owner API/CLI ingestion, isolated from the user's library."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_ingest
from knowledge_mcp import Knowledge
from server import Server
from vault import Vault


class AgentIngestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.vault = Vault(self.root/'library', start_worker=False)
        self.addCleanup(self.vault.close)
        self.document = self.root/'已完成.md'
        self.document.write_text('锦鲤项目的验收证据已经完成。')

    def complete(self, submission):
        with self.vault.db() as c:
            job = dict(c.execute('SELECT * FROM jobs WHERE id=?', (submission['job_id'],)).fetchone())
        self.vault.run_job(job)
        return agent_ingest.receipt(self.vault, submission['job_id'])

    def test_explicit_file_defaults_to_in_place_and_is_searchable_only_after_completion(self):
        job = agent_ingest.submit(self.vault, {'paths': [str(self.document)], 'request_id': 'worker-1:version-1'})
        queued = agent_ingest.receipt(self.vault, job['job_id'])
        self.assertFalse(queued['index_ready'])
        done = self.complete(job)
        self.assertTrue(done['index_ready'])
        self.assertTrue(done['files'][0]['text_indexed'])
        found = Knowledge(self.vault.root).search('锦鲤')['results'][0]
        self.assertEqual(found['id'], done['files'][0]['id'])
        self.assertIsNone(self.vault.file_row(found['id'])['vault_path'])
        self.assertTrue(self.document.is_file())

    def test_idempotent_retry_and_new_version_keep_identity_and_old_citation(self):
        payload = {'paths': [str(self.document)], 'request_id': 'work-v1'}
        first = agent_ingest.submit(self.vault, payload)
        receipt = self.complete(first)
        hit = Knowledge(self.vault.root).search('锦鲤')['results'][0]
        self.document.write_text('紫竹项目的第二版工作产物。')
        again = agent_ingest.submit(self.vault, payload)
        self.assertEqual(first['job_id'], again['job_id'])
        self.assertTrue(again['reused'])
        second = self.complete(agent_ingest.submit(self.vault, {**payload, 'request_id': 'work-v2'}))
        self.assertEqual(second['files'][0]['id'], receipt['files'][0]['id'])
        old = agent_ingest.receipt(self.vault, first['job_id'])
        self.assertTrue(old['success'])
        self.assertFalse(old['index_ready'])
        self.assertFalse(old['files'][0]['current_version'])
        self.assertTrue(Knowledge(self.vault.root).passage(hit['id'], hit['passage_id'])['historical'])
        self.document.unlink()
        self.assertEqual(agent_ingest.submit(self.vault, payload)['job_id'], first['job_id'])

    def test_reusing_request_for_different_files_has_no_source_side_effect(self):
        payload = {'paths': [str(self.document)], 'request_id': 'same'}
        agent_ingest.submit(self.vault, payload)
        other = self.root/'other.md'; other.write_text('其他资料')
        with self.assertRaisesRegex(ValueError, 'request_id'):
            agent_ingest.submit(self.vault, {**payload, 'paths': [str(other)]})
        with self.vault.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sources').fetchone()[0], 1)

    def test_bad_batch_rejected_before_any_source_and_symlink_file_is_not_followed(self):
        for paths in [[str(self.document), str(self.root/'missing.md')],
                      [str(self.root)], ['relative.md'], 'not-an-array', [42], []]:
            with self.assertRaises(ValueError):
                agent_ingest.submit(self.vault, {'paths': paths})
        link = self.root/'linked.md'; link.symlink_to(self.document)
        with self.assertRaises(ValueError):
            agent_ingest.submit(self.vault, {'paths': [str(link)]})
        with self.assertRaises(ValueError):
            agent_ingest.submit(self.vault, {'paths': [str(self.vault.dbpath)]})
        with self.vault.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sources').fetchone()[0], 0)

    def test_queued_file_replacement_never_recursively_imports_new_directory(self):
        job = agent_ingest.submit(self.vault, {'paths': [str(self.document)]})
        self.document.unlink(); self.document.mkdir()
        (self.document/'not-selected.md').write_text('不应入库')
        done = self.complete(job)
        self.assertFalse(done['index_ready'])
        self.assertEqual(done['failure_count'], 1)
        self.assertEqual(self.vault.list_files()['total'], 0)

    def test_queued_changed_file_fails_until_new_request_and_binary_is_metadata_only(self):
        job = agent_ingest.submit(self.vault, {'paths': [str(self.document)]})
        self.document.write_text('被修改的文本长度不同')
        self.assertFalse(self.complete(job)['index_ready'])
        binary = self.root/'binary.unknown'; binary.write_bytes(b'\x00\x01\x02')
        done = self.complete(agent_ingest.submit(self.vault, {'paths': [str(binary)], 'mode': 'managed'}))
        self.assertTrue(done['index_ready'])
        self.assertFalse(done['files'][0]['text_indexed'])
        self.assertTrue(self.vault.file_row(done['files'][0]['id'])['vault_path'])
        self.assertEqual(binary.read_bytes(), b'\x00\x01\x02')

    def test_client_refuses_remote_and_public_or_symlinked_descriptors(self):
        descriptor = self.vault.root/'runtime.json'
        descriptor.write_text(json.dumps({'origin':'https://example.invalid', 'token':'private-token-1234'})); descriptor.chmod(0o600)
        client = agent_ingest.Client(self.vault.root)
        with self.assertRaisesRegex(ValueError, '本机'):
            client.session()
        descriptor.write_text(json.dumps({'origin':'http://127.0.0.1:12345', 'token':'private-token-1234'})); descriptor.chmod(0o644)
        with self.assertRaisesRegex(ValueError, '600'):
            client.session()
        descriptor.unlink(); descriptor.symlink_to(self.document)
        with self.assertRaises(ValueError):
            client.session()

    def test_wait_timeout_is_distinct_and_async_is_explicit(self):
        from types import SimpleNamespace
        args = SimpleNamespace(schema=False, data=str(self.vault.root), job=None, paths=[str(self.document)], request_id='test', mode='in-place', wait=.01)
        queued = {'job_id':'a'*32, 'state':'queued', 'complete':False, 'index_ready':False}
        with patch.object(agent_ingest.Client, 'request', side_effect=[{'job_id':'a'*32}, queued, queued]):
            result, code = agent_ingest.run(args)
        self.assertEqual(code, 3); self.assertTrue(result['timed_out']); self.assertFalse(result['index_ready'])
        args.wait = 0
        with patch.object(agent_ingest.Client, 'request', side_effect=[{'job_id':'a'*32}, queued]):
            result, code = agent_ingest.run(args)
        self.assertEqual(code, 0); self.assertFalse(result['index_ready']); self.assertEqual(result['state'], 'queued')

    def test_connection_loss_keeps_request_or_job_identity_for_safe_retry(self):
        from types import SimpleNamespace
        args = SimpleNamespace(schema=False, data=str(self.vault.root), job=None, paths=[str(self.document)], request_id='recoverable', mode='in-place', wait=2)
        with patch.object(agent_ingest.Client, 'request', side_effect=ValueError('连接中断')):
            result, code = agent_ingest.run(args)
        self.assertEqual(code, 2); self.assertEqual(result['request_id'], 'recoverable')
        with patch.object(agent_ingest.Client, 'request', side_effect=[{'job_id':'b'*32, 'request_id':'recoverable'}, ValueError('连接中断')]):
            result, code = agent_ingest.run(args)
        self.assertEqual(code, 2); self.assertEqual(result['job_id'], 'b'*32)
        self.assertEqual(result['request_id'], 'recoverable')

    def test_cli_against_real_http_returns_completed_index_and_never_prints_token(self):
        self.vault.close()
        self.vault = Vault(self.root/'library')
        self.addCleanup(self.vault.close)
        server = Server(('127.0.0.1', 0), self.vault)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        descriptor = self.vault.root/'runtime.json'
        descriptor.write_text(json.dumps({'origin':server.origin, 'token':server.token})); descriptor.chmod(0o600)
        command = [sys.executable, '-B', str(Path(agent_ingest.__file__)), '--data', str(self.vault.root), '--request-id', 'cli-worker:report-v1', '--wait', '15', str(self.document)]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(data['index_ready']); self.assertTrue(data['files'][0]['text_indexed'])
            self.assertNotIn(server.token, result.stdout + result.stderr)
            self.assertEqual(Knowledge(self.vault.root).search('锦鲤')['results'][0]['id'], data['files'][0]['id'])
            second = subprocess.run(command, text=True, capture_output=True, timeout=25)
            self.assertTrue(json.loads(second.stdout)['reused'])
            self.assertEqual(self.vault.list_files()['total'], 1)
            import urllib.request, urllib.error
            request = urllib.request.Request(server.origin+'/api/ingest/files?token='+server.token,
                      data=json.dumps({'paths':[str(self.document)]}).encode(), headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown(); thread.join(5); server.server_close()


if __name__ == '__main__':
    unittest.main()
