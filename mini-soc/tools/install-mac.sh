#!/bin/bash
# Build the menu-bar app + widget, install to /Applications, and leave no second copy behind.
# macOS's widget service silently drops a widget when two apps with the same bundle ID are
# registered (the build output and the installed app), so the build copy is unregistered and deleted.
set -euo pipefail
cd "$(dirname "$0")/../mac"
LSR=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
DD="$(mktemp -d)"
xcodegen generate -q
xcodebuild -project QwenMiniSOC.xcodeproj -scheme QwenMiniSOC -configuration Release \
  -derivedDataPath "$DD" -allowProvisioningUpdates -quiet build
BUILT="$DD/Build/Products/Release/QwenMiniSOC.app"
codesign --verify --deep --strict "$BUILT"
pkill -x QwenMiniSOC || true
rm -rf /Applications/QwenMiniSOC.app
cp -R "$BUILT" /Applications/
"$LSR" -u "$BUILT" 2>/dev/null || true
rm -rf "$DD"
"$LSR" -f -R /Applications/QwenMiniSOC.app
pluginkit -a /Applications/QwenMiniSOC.app/Contents/PlugIns/SOCWidget.appex
open /Applications/QwenMiniSOC.app
echo "installed; registered copies: $("$LSR" -dump | grep -c 'path: .*QwenMiniSOC.app ')"
