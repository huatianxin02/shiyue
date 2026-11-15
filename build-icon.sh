#!/bin/bash
# Rebuilds assets/AppIcon.icns from assets/icon.svg.
#
# Chrome is only used as an SVG rasteriser: it is already on this machine and
# renders gradients, filters and CJK text exactly like the web view does. Any
# renderer that honours --screenshot could replace it.
set -euo pipefail
cd "$(dirname "$0")"
SVG="assets/icon.svg"
ICNS="assets/AppIcon.icns"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -f "$SVG" ] || { echo "缺少 $SVG" >&2; exit 1; }
[ -x "$CHROME" ] || { echo "找不到 Chrome，可用 CHROME=/path/to/chrome 指定" >&2; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
"$CHROME" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
  --default-background-color=00000000 --window-size=1024,1024 \
  --screenshot="$WORK/master.png" "file://$PWD/$SVG" >/dev/null 2>&1
[ -s "$WORK/master.png" ] || { echo "渲染失败" >&2; exit 1; }

SET="$WORK/AppIcon.iconset"; mkdir -p "$SET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$WORK/master.png" --out "$SET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size*2)) $((size*2)) "$WORK/master.png" --out "$SET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$ICNS"
echo "已生成：$ICNS"
