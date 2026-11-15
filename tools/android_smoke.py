#!/usr/bin/env python3
"""Generate synthetic Mac exports and exercise Android's pack reader on a JVM."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vault import Vault


def fixture(destination):
    with tempfile.TemporaryDirectory(prefix='shiyue-android-fixture-') as td:
        base = Path(td); inputs = base/'inputs'; inputs.mkdir()
        (inputs/'note.md').write_text('# Synthetic note\nOffline knowledge example.\n')
        (inputs/'reference.txt').write_text('Synthetic reference for pack verification.\n')
        (inputs/'table.csv').write_text('name,value\nexample,42\n')
        (inputs/'pixel.png').write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1kAAAAASUVORK5CYII='))
        v = Vault(base/'vault', start_worker=False)
        try:
            sid = v.add_source(inputs, 'managed')
            ids = {p.name:v.ingest(p, sid, 'managed') for p in sorted(inputs.iterdir())}
            one = v.create_collection('AI资料')['id']; two = v.create_collection('参考')['id']
            v.assign_collection(one, [ids['pixel.png'], ids['note.md']])
            v.assign_collection(two, [ids['reference.txt'], ids['table.csv']])
            jid = v.queue('export', {})['job_id']
            with v.db() as c: job = dict(c.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
            v.run_job(job)
            with v.db() as c: job = dict(c.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())
            if job['state'] != 'done': raise RuntimeError(job['error'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Never replace an explicitly supplied fixture.
            with open(json.loads(job['result'])['path'], 'rb') as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst)
        finally: v.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, help='Create fixture only, refusing an existing path')
    args = parser.parse_args()
    if args.fixture:
        fixture(args.fixture); print('Synthetic fixture created'); return
    java_home = os.environ.get('JAVA_HOME')
    javac = str(Path(java_home)/'bin/javac') if java_home else shutil.which('javac')
    java = str(Path(java_home)/'bin/java') if java_home else shutil.which('java')
    if not javac or not java: raise SystemExit('Install a JDK or set JAVA_HOME')
    with tempfile.TemporaryDirectory(prefix='shiyue-android-smoke-') as td:
        base = Path(td); pack = base/'fixture.vaultpack'; fixture(pack)
        classes = base/'classes'; classes.mkdir()
        sources = [ROOT/'android/src/local/shiyue/vault'/name for name in ['Json.java','Pack.java']]
        sources.append(ROOT/'android/test/local/shiyue/vault/PackTest.java')
        subprocess.run([javac,'-encoding','UTF-8','-d',str(classes), *map(str,sources)], check=True)
        subprocess.run([java,'-cp',str(classes),'local.shiyue.vault.PackTest',str(pack),str(base/'roundtrip')], check=True)


if __name__ == '__main__': main()
