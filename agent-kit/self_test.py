"""Portable acceptance runner. The suite creates and destroys its own library."""
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import tempfile


def run_self_test(app):
    kit = Path(__file__).resolve().parent
    manifest = json.loads((kit / 'checksums.json').read_text())
    checked = []
    for name, digest in manifest['files'].items():
        path = kit / name
        if path.resolve().parent != kit and kit not in path.resolve().parents:
            raise ValueError('校验清单包含外部路径')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('接入包校验失败：' + name)
        checked.append(name)
    required = {'shiyue_agent.py', 'self_test.py', 'tests/test_agent_kit.py'}
    if not required <= set(checked): raise ValueError('接入包校验清单不完整')
    app = app.resolve()
    python = app / 'Contents/Resources/python/bin/python3'
    runtime = app / 'Contents/Resources/runtime'
    if not python.is_file() or not (runtime / 'server.py').is_file():
        raise ValueError('指定 App 缺少内置 Python 或服务端')
    signature = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], capture_output=True, text=True, timeout=30)
    if signature.returncode: raise ValueError('指定 App 签名校验失败，请重新复制完整安装包')
    environment = {**os.environ, 'SHIYUE_TEST_APP': str(app), 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'}
    for key in ('PYTHONPATH', 'PYTHONHOME', 'SHIYUE_DATA'): environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix='shiyue-self-test-') as scratch:
        environment['TMPDIR'] = scratch
        process = subprocess.Popen([str(python), '-B', '-m', 'unittest', 'discover', '-s', str(kit / 'tests'), '-p', 'test_agent_kit.py', '-v'],
                                   cwd=kit, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError('自检超过三分钟，已停止隔离测试进程并清理临时库')
    # Test errors are bounded and contain only generated sample data.
    return {'ok': process.returncode == 0, 'kit_version': '1.3.0', 'app': str(app),
            'verified_files': len(checked), 'app_signature_verified': True,
            'library': 'temporary, created by tests and removed on completion', 'live_library_accessed': False,
            'log': (stdout + stderr)[-20000:]}
