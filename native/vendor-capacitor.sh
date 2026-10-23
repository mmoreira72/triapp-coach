#!/bin/sh
# SPM hangs downloading Capacitor's binary xcframeworks on this network (curl works). This builds a local copy of
# capacitor-swift-pm with the binaries as path targets and points Xcode at it (mirrors.json). Run after npm install, and again
# when @capacitor/ios changes version.
set -e
cd "$(dirname "$0")"
V=$(node -p "require('@capacitor/ios/package.json').version")
M="$PWD/ios/vendor/capacitor-swift-pm"
rm -rf "$M" && git clone -q --depth 1 --branch "$V" https://github.com/ionic-team/capacitor-swift-pm.git "$M"
cd "$M" && rm -rf .git && mkdir Binaries
for f in Capacitor Cordova; do
  curl -sL -o "/tmp/$f.zip" "https://github.com/ionic-team/capacitor-swift-pm/releases/download/$V/$f.xcframework.zip"
  want=$(grep -A2 "$f.xcframework.zip" Package.swift | grep -o '[0-9a-f]\{64\}')
  [ "$(shasum -a 256 "/tmp/$f.zip" | cut -d' ' -f1)" = "$want" ] || { echo "checksum mismatch for $f"; exit 1; }
  (cd Binaries && unzip -q "/tmp/$f.zip")
done
python3 - <<'PY'
import re
s = open("Package.swift").read()
for f in ("Capacitor", "Cordova"):
    s = re.sub(rf'url: "[^"]*{f}\.xcframework\.zip",\s*checksum: "[0-9a-f]+"', f'path: "Binaries/{f}.xcframework"', s)
open("Package.swift", "w").write(s)
PY
git init -q && git add -A -f && git -c user.name=vendor -c user.email=vendor@local commit -qm "capacitor-swift-pm $V, local binaries" && git tag "$V"
C="$OLDPWD/ios/App/App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/configuration"
mkdir -p "$C"
printf '{\n  "object" : [ { "mirror" : "file://%s", "original" : "https://github.com/ionic-team/capacitor-swift-pm.git" } ],\n  "version" : 1\n}\n' "$M" > "$C/mirrors.json"
rm -f "$OLDPWD/ios/App/App.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved" ~/Library/org.swift.swiftpm/security/fingerprints/capacitor-swift-pm*
rm -rf ~/Library/Caches/org.swift.swiftpm/repositories/capacitor-swift-pm*
echo "capacitor-swift-pm $V vendored at $M"
