#!/usr/bin/env bash
# Regenerate Mason Jar app icons from assets/icons/icon.png (square 1024×1024 master).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICON_DIR="$ROOT/assets/icons"
ICONSET="$ICON_DIR/icon.iconset"
MASTER="$ICON_DIR/icon.png"

if ! command -v sips >/dev/null 2>&1; then
  echo "sips is required (macOS)." >&2
  exit 1
fi

mkdir -p "$ICONSET"

# Square 1024×1024 master (center-crop non-square sources).
WORK="$ICON_DIR/.icon-build-tmp.png"
cp "$MASTER" "$WORK"
read -r W H < <(sips -g pixelWidth -g pixelHeight "$WORK" 2>/dev/null | awk '/pixelWidth|pixelHeight/ {print $2}' | paste - -)
if [[ "$W" != "$H" ]]; then
  SIDE=$(( W < H ? W : H ))
  sips -c "$SIDE" "$SIDE" "$WORK" --out "$WORK" >/dev/null
fi
sips -z 1024 1024 "$WORK" --out "$MASTER" >/dev/null
rm -f "$WORK"

make_png() {
  local size=$1
  local out=$2
  sips -z "$size" "$size" "$MASTER" --out "$out" >/dev/null
}

make_png 16  "$ICONSET/icon_16x16.png"
make_png 32  "$ICONSET/icon_16x16@2x.png"
make_png 32  "$ICONSET/icon_32x32.png"
make_png 64  "$ICONSET/icon_32x32@2x.png"
make_png 128 "$ICONSET/icon_128x128.png"
make_png 256 "$ICONSET/icon_128x128@2x.png"
make_png 256 "$ICONSET/icon_256x256.png"
make_png 512 "$ICONSET/icon_256x256@2x.png"
make_png 512 "$ICONSET/icon_512x512.png"
make_png 1024 "$ICONSET/icon_512x512@2x.png"

if command -v iconutil >/dev/null 2>&1; then
  iconutil -c icns "$ICONSET" -o "$ICON_DIR/icon.icns"
  echo "Wrote $ICON_DIR/icon.icns"
else
  echo "iconutil not found; skipped .icns" >&2
fi

if command -v convert >/dev/null 2>&1; then
  convert "$MASTER" -define icon:auto-resize=256,128,64,48,32,16 "$ICON_DIR/icon.ico"
  echo "Wrote $ICON_DIR/icon.ico"
else
  echo "ImageMagick convert not found; skipped .ico (macOS .icns updated)" >&2
fi

echo "Icon build complete."
