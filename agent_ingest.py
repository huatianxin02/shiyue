#!/usr/bin/env python3
"""Explicit local agent output ingestion; read-only knowledge MCP stays unchanged.

Requires the already running Vault and its per-user runtime.json session. This CLI
never launches a second writer, uploads files, or copies credentials into output.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import urllib.error
import urllib.request
import uuid

TERMINAL = {'done', 'partial', 'failed', 'cancelled'}
REQUEST_PREFIX = 'agent-ingest:'


def schema():
    return {
        'version': 1,
        'submit': {'method': 'POST', 'path': '/api/ingest/files', 'input': {
            'type': 'object', 'additionalProperties': False, 'required': ['paths'],
            'properties': {
                'paths': {'type': 'array', 'minItems': 1, 'maxItems': 1000,
                          'items': {'type': 'string'}, 'description': '已完成的本地普通文件绝对路径，不接受目录或符号链接文件'},
                'mode': {'enum': ['in-place', 'managed'], 'default': 'in-place'},
                'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 120,
                               'description': '重试复用；文件新版本必须使用新标识。不填则生成并返回。'},
            }}},
        'receipt': {'method': 'GET', 'path': '/api/ingest/jobs/{job_id}'},
        'authentication': '从当前资料库的 runtime.json 读取 origin/token；X-Vault-Token 请求头，仅限 127.0.0.1，禁止 URL token。令牌不会由 CLI 输出。',
        'semantics': {
            'queued': '仅接受任务，不等于完成索引',
            'index_ready': '本次全部文件已完成且仍为当前版本，文件检索索引可用',
            'text_indexed': '单个文件存在可搜索正文；false 时仅按名称等元数据检索',
            'partial': '部分失败，检查 failure_count/errors，不得当作整批成功',
            'retry': '重复 request_id 返回原任务；变更路径或模式会被拒绝。再次处理新版文件使用新 request_id。',
            'scope': '显式写入接口使用本机所有者会话，不属于只读 MCP；不修改 AI 读取范围。',
        },
    }


def _request_id(value):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not 1 <= len(value) <= 120 or any(ord(ch) < 32 for ch in value):
        raise ValueError('request_id 必须是 1–120 字符的非控制字符字符串')
    return value


def _paths(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 1000:
        raise ValueError('paths 必须包含 1–1000 个已完成文件')
    normalized = []
    for value in values:
        if not isinstance(value, str) or not value or '\x00' in value or len(value) > 8192:
            raise ValueError('文件路径无效')
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError('请提供文件绝对路径')
        # Keep canonical paths in the durable task. Parent aliases such as /tmp
        # resolve before guards are captured; a symlink file itself is rejected.
        if path.is_symlink():
            raise ValueError('请提交实际文件，不接受符号链接文件')
        path = path.resolve()
        if str(path) not in normalized:
            normalized.append(str(path))
    return normalized


def submit(vault, payload):
    """Validate the entire batch before any source is created, then queue once."""
    if not isinstance(payload, dict) or set(payload) - {'paths', 'mode', 'request_id'}:
        raise ValueError('仅支持 paths、mode、request_id')
    paths = _paths(payload.get('paths'))
    mode = payload.get('mode', 'in-place')
    if mode not in ('in-place', 'managed'):
        raise ValueError('mode 必须为 in-place 或 managed')
    request = _request_id(payload.get('request_id'))
    internal_request = REQUEST_PREFIX + request
    job_id = uuid.uuid5(uuid.NAMESPACE_URL, 'shiyue-import:' + internal_request).hex
    with vault.lock:
        with vault.db() as connection:
            prior = connection.execute('SELECT kind,payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        if prior:
            previous = json.loads(prior['payload'])
            if (prior['kind'] != 'import' or previous.get('mode') != mode or
                    [item['path'] for item in previous.get('items', [])] != paths):
                raise ValueError('同一 request_id 不能用于不同文件或保存方式')
            return {'job_id': job_id, 'request_id': request, 'reused': True,
                    'receipt_url': '/api/ingest/jobs/' + job_id}
        from disk_discovery import candidate_guard
        guards = {}
        for value in paths:
            path = Path(value)
            if path == vault.root or vault.root in path.parents:
                raise ValueError('不能索引资料库自身文件')
            if not path.is_file():
                raise ValueError('仅接受已完成的普通文件，不接受目录或缺失路径：' + str(path))
            guards[value] = candidate_guard(path)
        queued = vault.submit_import(paths, mode, internal_request, file_guards=guards)
    return {**queued, 'request_id': request, 'reused': False,
            'receipt_url': '/api/ingest/jobs/' + queued['job_id']}


def receipt(vault, job_id):
    """A successful queue request is never labelled a completed text extraction."""
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('任务标识无效')
    with vault.db() as connection:
        connection.execute('BEGIN')
        row = connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['kind'] != 'import':
            raise ValueError('导入任务不存在')
        payload = json.loads(row['payload'])
        result = json.loads(row['result']) if row['result'] else {}
        files = []
        for file in result.get('files', []):
            current = connection.execute('SELECT sha FROM files WHERE id=?', (file['id'],)).fetchone()
            obj = connection.execute('SELECT chunks_json,warning FROM objects WHERE sha=?', (file['sha'],)).fetchone()
            chunks = json.loads(obj['chunks_json']) if obj else []
            text_count = sum(bool(chunk.get('text', '').strip()) for chunk in chunks)
            same = bool(current and current['sha'] == file['sha'])
            indexed = same and bool(connection.execute('SELECT 1 FROM search WHERE file_id=? LIMIT 1', (file['id'],)).fetchone())
            files.append({**file, 'sha256': file['sha'], 'current_version': same,
                          'index_ready': indexed, 'text_indexed': bool(indexed and text_count),
                          'text_sections': text_count, 'warning': obj['warning'] if obj else '历史对象已不存在',
                          'file_uri': 'shiyue://files/' + file['id']})
        requested = len(payload.get('items', []))
        complete = row['state'] in TERMINAL
        success = (row['state'] == 'done' and not result.get('failure_count', 0)
                   and result.get('imported', 0) == requested and requested > 0)
        return {'job_id': job_id, 'state': row['state'], 'complete': complete,
                'success': success, 'index_ready': bool(success and len(files) == requested and all(f['index_ready'] for f in files)),
                'requested': requested, 'total': row['total'], 'done': row['done'],
                'imported': result.get('imported', 0), 'failure_count': result.get('failure_count', 0),
                'error': row['error'], 'errors': result.get('errors', []), 'files': files}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('本机索引服务不能重定向')


class Client:
    def __init__(self, data):
        self.data = Path(data).expanduser()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def session(self):
        descriptor = self.data / 'runtime.json'
        try:
            descriptor_stat = descriptor.lstat()
            if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_uid != os.getuid() or descriptor_stat.st_mode & 0o077:
                raise ValueError('runtime.json 必须是当前用户拥有的私有普通文件（权限 600）')
            fd = os.open(descriptor, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (descriptor_stat.st_dev, descriptor_stat.st_ino):
                    raise ValueError('连接描述发生变化，请重试')
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError('本机连接描述无效')
            session = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            raise ValueError('请先打开拾页；自定义资料库请用 --data 指定正在运行的库目录') from None
        if not isinstance(session, dict):
            raise ValueError('本机连接描述无效')
        origin = session.get('origin', '')
        if not isinstance(origin, str) or not re.fullmatch(r'http://127\.0\.0\.1:([1-9][0-9]{0,4})', origin):
            raise ValueError('只允许连接本机拾页服务')
        if not 1 <= int(origin.rsplit(':', 1)[1]) <= 65535:
            raise ValueError('本机端口无效')
        token = session.get('token')
        if not isinstance(token, str) or not 16 <= len(token) <= 1024 or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
            raise ValueError('本机会话无效，请重启拾页')
        return origin, token

    def request(self, route, payload=None):
        origin, token = self.session()
        request = urllib.request.Request(origin + route,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
            headers={'X-Vault-Token': token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('索引回执过大，请缩小每批文件数')
            return json.loads(raw)
        except urllib.error.HTTPError as error:
            try: message = json.loads(error.read(65536)).get('error', '索引请求失败')
            except (ValueError, OSError): message = '索引请求失败'
            # Never stringify Request objects or the runtime descriptor.
            raise ValueError(str(message)) from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            raise ValueError('无法连接拾页，请确认软件正在运行；已提交任务可使用 --job 查询') from None


def run(args):
    if args.schema:
        return schema(), 0
    if not math.isfinite(args.wait) or not 0 <= args.wait <= 3600:
        raise ValueError('--wait 必须在 0–3600 秒之间')
    client = Client(args.data)
    if args.job:
        if args.paths or args.request_id:
            raise ValueError('--job 查询不能同时提交文件或 request_id')
        if not re.fullmatch(r'[0-9a-f]{32}', args.job):
            raise ValueError('任务标识无效')
        accepted = {'job_id': args.job}
    else:
        request_id = _request_id(args.request_id)
        try:
            accepted = client.request('/api/ingest/files', {'paths': [str(Path(p).expanduser().absolute()) for p in args.paths],
                               'mode': args.mode, 'request_id': request_id})
        except ValueError as error:
            return {'ok': False, 'request_id': request_id, 'error': str(error),
                    'notice': '若无法确定提交是否成功，修复连接后使用相同 request_id 重试'}, 2
    deadline = time.monotonic() + args.wait
    while True:
        try:
            current = client.request('/api/ingest/jobs/' + accepted['job_id'])
        except ValueError as error:
            return {**accepted, 'ok': False, 'error': str(error),
                    'notice': '任务已提交；连接恢复后使用 --job 查询，不要重复创建任务'}, 2
        output = {**accepted, **current}
        if current['complete']:
            return output, 0 if current['index_ready'] else 2
        if args.wait == 0:
            return output, 0  # Explicit async mode; state/index_ready remain unambiguous.
        if time.monotonic() >= deadline:
            return {**output, 'timed_out': True, 'notice': '等待超时，任务仍在队列中；用 --job 查询，不要换 request_id 重复提交'}, 3
        time.sleep(min(.25, max(0, deadline - time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser(description='将 agent 已完成的本地文件加入拾页索引，输出 JSON 回执')
    parser.add_argument('paths', nargs='*', help='已完成的文件路径；不接受文件夹')
    parser.add_argument('--data', default=str(Path.home()/'Library/Application Support/拾页资料库'))
    parser.add_argument('--mode', choices=['in-place', 'managed'], default='in-place')
    parser.add_argument('--request-id', help='任务与产出版本的唯一标识；网络重试复用')
    parser.add_argument('--job', help='只查询已有导入任务')
    parser.add_argument('--wait', type=float, default=120, help='等待索引秒数，0 只提交，默认 120，上限 3600')
    parser.add_argument('--schema', action='store_true', help='输出接口契约，无需启动拾页')
    args = parser.parse_args(argv)
    try:
        result, code = run(args)
    except (ValueError, OSError) as error:
        result, code = {'ok': False, 'error': str(error)}, 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
