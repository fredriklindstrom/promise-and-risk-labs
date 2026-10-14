#!/bin/bash
# Build "Bench Monitor.app" (ad-hoc signed). Bundle id: io.github.fredriklindstrom.benchmonitor
# Output: build/Bench Monitor.app.  Pass --open to launch it afterwards.
# Ad-hoc signed only: fine on the Mac that built it. Sign with a Developer ID and notarize before
# distributing a binary.
set -euo pipefail
cd "$(dirname "$0")"

swift build -c release
APP="build/Bench Monitor.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/release/BenchMonitor "$APP/Contents/MacOS/BenchMonitor"

# App icon: Resources/AppIcon-1024.png (made from Resources/logo-source.png by tools/make_icon.swift)
# -> every size macOS asks for -> AppIcon.icns. Apple tools only (sips, iconutil).
if [ -f Resources/AppIcon-1024.png ]; then
  ICONSET="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s Resources/AppIcon-1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) Resources/AppIcon-1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  rm -rf "$(dirname "$ICONSET")"
else
  echo "no Resources/AppIcon-1024.png: building without an app icon"
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Bench Monitor</string>
  <key>CFBundleDisplayName</key><string>Bench Monitor</string>
  <key>CFBundleIdentifier</key><string>io.github.fredriklindstrom.benchmonitor</string>
  <key>CFBundleExecutable</key><string>BenchMonitor</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>© 2026 Fredrik Lindstrom.</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP"
echo "Built $APP"

if [ "${1:-}" = "--open" ]; then
  open "$APP"
fi
