#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || { echo '当前应用构建支持 Apple Silicon macOS。' >&2; exit 1; }
python3 prepare-runtime.py
BUILD_STAGE=$(mktemp -d "$PWD/.app-build-XXXXXX")
trap 'rm -rf "$BUILD_STAGE"' EXIT
APP="$BUILD_STAGE/拾页资料库.app"
VERSION=$(python3 -c "import re;print(re.search(r\"VERSION = '([^']+)'\",open('vault.py').read()).group(1))")
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/runtime/web" "$APP/Contents/Resources/runtime/vendor" "$APP/Contents/Resources/runtime/bin"
mkdir -p bin
swiftc -O -file-prefix-map "$PWD"=/src/shiyue -debug-prefix-map "$PWD"=/src/shiyue -parse-as-library -target arm64-apple-macosx13.0 -module-cache-path "$PWD/.build-cache" native/MediaProbe.swift -framework Cocoa -framework AVFoundation -framework ImageIO -framework UniformTypeIdentifiers -o bin/MediaProbe
swiftc -O -file-prefix-map "$PWD"=/src/shiyue -debug-prefix-map "$PWD"=/src/shiyue -parse-as-library -target arm64-apple-macosx13.0 -module-cache-path "$PWD/.build-cache" native/DocumentProbe.swift -framework Cocoa -framework PDFKit -framework Vision -framework ImageIO -framework UniformTypeIdentifiers -o bin/DocumentProbe
swiftc -O -file-prefix-map "$PWD"=/src/shiyue -debug-prefix-map "$PWD"=/src/shiyue -target arm64-apple-macosx13.0 -module-cache-path "$PWD/.build-cache" native/EventWatch.swift -framework CoreServices -o bin/EventWatch
cp document_extract.py knowledge_filters.py organization.py collection_tree.py disk_discovery.py agent_ingest.py agent_catalog.py chinese_search.py knowledge_index.py source_watch.py backup_store.py knowledge_mcp.py vault.py vault_features.py server.py media.py safe_content.py "$APP/Contents/Resources/runtime/"
# media.py resolves the helper relative to itself, so the binary ships inside runtime/.
cp bin/EventWatch bin/MediaProbe bin/DocumentProbe "$APP/Contents/Resources/runtime/bin/"
chmod +x "$APP/Contents/Resources/runtime/bin/MediaProbe"
cp web/organization.js web/discovery.js web/index.html web/app.js web/features.js web/style.css web/integration.html web/integration.js web/integration.css web/artifact-preview.js web/joint-backup.js "$APP/Contents/Resources/runtime/web/"
cp vendor-lock.json "$APP/Contents/Resources/"
cp jieba-lock.json "$APP/Contents/Resources/"
cp -R THIRD_PARTY_LICENSES "$APP/Contents/Resources/"
cp LICENSE THIRD_PARTY_NOTICES.md "$APP/Contents/Resources/"
python3 - "$APP/Contents/Resources/build-manifest.json" <<'PYMANIFEST'
import hashlib,json,sys
from pathlib import Path
paths=list(Path('native').glob('*.swift'))+list(Path('web').glob('*'))+list(Path('.').glob('*.py'))
paths += [Path(p) for p in ['build-app.sh','runtime-lock.json','vendor-lock.json']]
paths=[p for p in paths if p.is_file() and not p.name.startswith('delivery-')]
manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
Path(sys.argv[1]).write_text(json.dumps({'source_sha256':manifest},indent=2)+'\n')
PYMANIFEST
# The Dock icon is part of the bundle, so a copied .app keeps its identity.
cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
python3 - "$APP/Contents/Resources/runtime/vendor" <<'PY'
from pathlib import Path
import shutil,sys
out=Path(sys.argv[1])
if out.exists():shutil.rmtree(out)
shutil.copytree('vendor',out,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
PY

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleExecutable</key><string>Shiyue</string><key>CFBundleIdentifier</key><string>local.shiyue.vault</string><key>CFBundleName</key><string>拾页资料库</string><key>CFBundleIconFile</key><string>AppIcon</string><key>CFBundleDisplayName</key><string>拾页资料库</string><key>CFBundlePackageType</key><string>APPL</string><key>CFBundleShortVersionString</key><string>1.0.0</string><key>CFBundleVersion</key><string>1</string><key>LSMinimumSystemVersion</key><string>13.0</string><key>NSHighResolutionCapable</key><true/><key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict></dict></plist>
PLIST
/usr/libexec/PlistBuddy -c 'Add :CFBundleURLTypes array' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Add :CFBundleURLTypes:0 dict' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Add :CFBundleURLTypes:0:CFBundleURLName string local.shiyue.citation' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes array' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string shiyue' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP/Contents/Info.plist"
swiftc -O -file-prefix-map "$PWD"=/src/shiyue -debug-prefix-map "$PWD"=/src/shiyue -target arm64-apple-macosx13.0 -module-cache-path "$PWD/.build-cache" native/App.swift -framework Cocoa -framework WebKit -o "$APP/Contents/MacOS/Shiyue"
python3 - "$APP/Contents/Resources/python" <<'PYRUNTIME'
from pathlib import Path
import shutil,sys,json,hashlib,subprocess
source=Path('runtime-download/python');target=Path(sys.argv[1])
lock=json.loads(Path('runtime-lock.json').read_text())
archive=Path('runtime-download')/lock['name']
assert hashlib.sha256(archive.read_bytes()).hexdigest()==lock['digest'].split(':')[1]
if target.exists():shutil.rmtree(target)
shutil.copytree(source,target,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copy2('runtime-lock.json',target.parent/'runtime-lock.json')
runtime=target.parent/'runtime'
for cache in runtime.rglob('__pycache__'):
    shutil.rmtree(cache)
# Sign nested Mach-O files before sealing the application; preserve relative links.
magic={b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe'}
for path in sorted(target.rglob('*')):
    if path.is_file() and not path.is_symlink():
        with path.open('rb') as stream: head=stream.read(4)
        if head in magic:subprocess.run(['codesign','--force','--sign','-',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
PYRUNTIME
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
if [ -e "$PWD/拾页资料库.app" ]; then
    mkdir -p backups/builds
    mv "$PWD/拾页资料库.app" "backups/builds/拾页资料库-$(date +%Y%m%d-%H%M%S)-$$.app"
fi
mv "$APP" "$PWD/拾页资料库.app"
printf '%s\n' "已构建：$PWD/拾页资料库.app"
