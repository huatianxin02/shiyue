#!/usr/bin/env python3
"""Restore the pinned build runtime; downloads only if its verified archive is absent."""
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()


root = Path(__file__).resolve().parent
if sys.version_info < (3, 12):
    raise SystemExit('构建需要 Python 3.12+；使用成品应用不需要安装 Python。')
lock = json.loads((root / 'runtime-lock.json').read_text())
cache = root / 'runtime-download'
cache.mkdir(exist_ok=True)
archive = cache / lock['name']
if not archive.exists():
    temporary = archive.with_suffix('.partial')
    try:
        subprocess.run(['/usr/bin/curl', '--fail', '--location', '--silent', '--show-error',
                        '--output', str(temporary), lock['browser_download_url']], check=True)
        if digest(temporary) != lock['digest'].split(':')[1]:
            raise ValueError('Python 下载校验失败')
        temporary.rename(archive)
    finally:
        temporary.unlink(missing_ok=True)
if digest(archive) != lock['digest'].split(':')[1]:
    raise SystemExit('Python 运行时归档校验失败，停止构建')
destination = cache / 'python'
# Extract from the pinned archive each build, so an edited local runtime cannot slip in.
stage = Path(tempfile.mkdtemp(dir=cache, prefix='.prepare-'))
try:
    with tarfile.open(archive) as bundle:
        bundle.extractall(stage, filter='data')
    if destination.exists():
        shutil.rmtree(destination)
    (stage / 'python').rename(destination)
finally:
    shutil.rmtree(stage, ignore_errors=True)
vendors = json.loads((root / 'vendor-lock.json').read_text())
actual = {str(path.relative_to(root / 'vendor')): digest(path)
          for path in (root / 'vendor').rglob('*')
          if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
if actual != vendors['sha256']:
    raise SystemExit('PDF 或中文分词组件与 vendor-lock.json 不一致，停止构建')
print('已校验固定 Python、PDF 与中文分词组件。')
