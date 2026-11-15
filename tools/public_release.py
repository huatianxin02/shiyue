#!/usr/bin/env python3
"""Allowlisted, privacy-checked source and macOS releases. Never copies personal QA/data."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = ['vault.py','vault_features.py','server.py','media.py','safe_content.py',
    'document_extract.py','knowledge_mcp.py','knowledge_index.py','knowledge_filters.py',
    'chinese_search.py','source_watch.py','backup_store.py','collection_tree.py',
    'organization.py','disk_discovery.py','agent_catalog.py','agent_ingest.py']
FILES = MODULES + ['README.md','LICENSE','SECURITY.md','CONTRIBUTING.md','CHANGELOG.md',
    'THIRD_PARTY_NOTICES.md','SBOM.cdx.json','.gitignore','.gitattributes',
    'build-app.sh','build-icon.sh','prepare-runtime.py','package-release.py',
    'runtime-lock.json','vendor-lock.json','jieba-lock.json','package.json','package-lock.json',
    '启动拾页.command','assets/AppIcon.icns','assets/icon.svg',
    'android/AndroidManifest.xml','android/build-android.sh','android/README.md',
    'android/test/build-audit.sh','android/test/audit/AndroidManifest.xml',
    '.github/workflows/ci.yml','.github/dependabot.yml','.github/ISSUE_TEMPLATE/bug_report.yml']
TREES = {'web':{'.html','.css','.js'}, 'native':{'.swift'}, 'tests':{'.py','.cjs','.swift'},
    'docs':{'.md'}, 'tools':{'.py'}, 'examples':{'.md'},
    'android/src':{'.java'}, 'android/res':{'.xml','.png'}, 'android/test/local':{'.java'}}
KIT = ['AGENTS.md','使用说明.md','给Agent的任务开头.txt','接口契约.json','checksums.json',
       'shiyue_agent.py','self_test.py','tests/test_agent_kit.py']
PRIVATE_NAMES = {'runtime.json','library.sqlite3','app.log','.env','.DS_Store'}
PRIVATE_SUFFIXES = {'.sqlite3','.sqlite','.db','.jks','.keystore','.p12','.pfx','.shiyuebackup','.vaultpack'}


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def privacy_issues(name, data):
    """Report reasons only; never echo the credential or personal path found."""
    issues=[];p=Path(name)
    if p.name in PRIVATE_NAMES or p.suffix in PRIVATE_SUFFIXES:issues.append('private file type')
    if re.search(rb'/Users/(?!Shared/|someone/|example/|user/|runner/|builder/)[A-Za-z0-9_.-]+/',data):
        issues.append('personal home path')
    if re.search(rb'(?<![A-Za-z0-9_-])(?:gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})',data):
        issues.append('credential pattern')
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data):issues.append('private key')
    for value in re.findall(rb'''["'](?:token|api_key|password)["']\s*[:=]\s*["']([A-Za-z0-9_-]{32,})["']''',data):
        if not value.startswith((b'test-',b'example-',b'dummy-')):issues.append('embedded credential value')
    return sorted(set(issues))


def scan(paths, root):
    errors=[]
    for path in paths:
        relative=str(path.relative_to(root))
        if path.is_symlink():
            try:path.resolve(strict=True).relative_to(root.resolve())
            except (ValueError,OSError):errors.append((relative,['external or broken symlink']))
        else:
            found=privacy_issues(relative,path.read_bytes())
            if found:errors.append((relative,found))
    if errors:raise ValueError('Public release blocked: '+json.dumps(errors,ensure_ascii=False))


def source_files(root=ROOT):
    names=set(FILES)|{'agent-kit/'+name for name in KIT}
    for directory,extensions in TREES.items():
        names.update(str(p.relative_to(root)) for p in (root/directory).rglob('*')
                     if p.is_file() and p.suffix in extensions and '__pycache__' not in p.parts)
    lock=json.loads((root/'vendor-lock.json').read_text())
    for name,digest in lock['sha256'].items():
        p=root/'vendor'/name
        if p.is_symlink() or not p.is_file() or sha(p)!=digest:raise ValueError('Vendor verification failed: '+name)
        names.add('vendor/'+name)
    names.update(str(p.relative_to(root)) for p in (root/'THIRD_PARTY_LICENSES').rglob('*') if p.is_file())
    paths=[root/name for name in sorted(names)]
    for p in paths:
        if not p.is_file() or p.is_symlink():raise ValueError('Missing or symlink source: '+str(p.relative_to(root)))
    scan(paths,root)
    return paths


def archive_tree(source, target):
    if target.exists():raise FileExistsError(target)
    fd,temporary=tempfile.mkstemp(prefix='.release-',suffix='.zip',dir=target.parent);os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for p in sorted(source.rglob('*')):
                if not p.is_file() and not p.is_symlink():continue
                info=zipfile.ZipInfo(source.name+'/'+str(p.relative_to(source)),(2026,11,15,0,0,0))
                info.create_system=3;info.compress_type=zipfile.ZIP_DEFLATED
                if p.is_symlink():
                    info.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(info,os.readlink(p))
                else:
                    info.external_attr=(stat.S_IFREG|(0o755 if os.access(p,os.X_OK) else 0o644))<<16
                    with z.open(info,'w',force_zip64=True) as out,p.open('rb') as inp:shutil.copyfileobj(inp,out)
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None:raise ValueError('Archive integrity check failed')
        # Publish without overwriting an existing release in a concurrent run.
        os.link(temporary,target)
    finally:Path(temporary).unlink(missing_ok=True)


def version(root=ROOT):
    return re.search(r"VERSION = '([^']+)'",(root/'vault.py').read_text()).group(1)


def export_source(output, root=ROOT):
    target=output/f'shiyue-{version(root)}-source'
    if target.exists():raise FileExistsError(target)
    paths=source_files(root)
    with tempfile.TemporaryDirectory(prefix='.source-',dir=output) as temp:
        stage=Path(temp)/target.name;stage.mkdir()
        manifest={}
        for source in paths:
            rel=source.relative_to(root);dest=stage/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest);manifest[str(rel)]=sha(dest)
        (stage/'SOURCE-MANIFEST.json').write_text(json.dumps({'version':version(root),'files':manifest},ensure_ascii=False,indent=2)+'\n')
        os.rename(stage,target)
    archive_tree(target,output/(target.name+'.zip'))
    return target


def export_app(output, root=ROOT):
    app=root/'拾页资料库.app';ver=version(root)
    if plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']!=ver:
        raise ValueError('Build the current app first')
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
    manifest=json.loads((app/'Contents/Resources/build-manifest.json').read_text())['source_sha256']
    for name,digest in manifest.items():
        if not (root/name).is_file() or sha(root/name)!=digest:raise ValueError('App is stale; rebuild after changing '+name)
    paths=[p for p in app.rglob('*') if p.is_file() or p.is_symlink()]
    scan(paths,app)
    runtime=app/'Contents/Resources/runtime'
    expected={str(p.relative_to(root)) for p in source_files(root) if str(p.relative_to(root)).startswith(('vendor/','web/'))}
    expected|=set(MODULES)|{'bin/MediaProbe','bin/DocumentProbe','bin/EventWatch'}
    actual={str(p.relative_to(runtime)) for p in runtime.rglob('*') if p.is_file()}
    if actual!=expected:raise ValueError('Unexpected or missing runtime files: '+str(sorted(actual^expected)))
    target=output/f'shiyue-{ver}-macos-arm64.zip'
    with tempfile.TemporaryDirectory(prefix='.app-release-',dir=output) as temp:
        stage=Path(temp)/f'shiyue-{ver}-macos-arm64';stage.mkdir()
        shutil.copytree(app,stage/app.name,symlinks=True)
        for name in ['README.md','LICENSE','SECURITY.md','CONTRIBUTING.md','THIRD_PARTY_NOTICES.md','SBOM.cdx.json']:
            shutil.copy2(root/name,stage/name)
        shutil.copytree(root/'THIRD_PARTY_LICENSES',stage/'THIRD_PARTY_LICENSES')
        shutil.copytree(root/'docs',stage/'docs')
        shutil.copy2(root/'CHANGELOG.md',stage/'CHANGELOG.md')
        for name in KIT:
            dest=stage/'agent-kit'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(root/'agent-kit'/name,dest)
        scan([p for p in stage.rglob('*') if p.is_file() or p.is_symlink()],stage)
        archive_tree(stage,target)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',action='store_true');parser.add_argument('--app',action='store_true')
    parser.add_argument('--check',action='store_true');parser.add_argument('--output',type=Path,default=ROOT/'dist')
    args=parser.parse_args()
    if args.check:
        files=source_files();print(json.dumps({'public_source_files':len(files),'privacy_gate':'passed'}));return
    if not args.source and not args.app:parser.error('Choose --source, --app or --check')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if args.source:export_source(output)
    if args.app:export_app(output)
    sums=''.join(sha(p)+'  '+p.name+'\n' for p in sorted(output.glob('shiyue-*.zip')))
    (output/'SHA256SUMS').write_text(sums)
    print(sums,end='')


if __name__=='__main__':main()
