#!/usr/bin/env bash
# Build dist/Repoboard.app with PyInstaller. macOS only. The app is signed ad hoc.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
build="$root/build/app"
app="$root/dist/Repoboard.app"
cd "$root"

[[ "$(uname -s)" == "Darwin" ]] || { echo "build-app.sh: macOS only" >&2; exit 1; }
rm -rf "$build" "$app"
mkdir -p "$build/Repoboard.iconset"

# The app icon in every size macOS asks for.
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" repoboard/resources/icon.png --out "$build/Repoboard.iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" repoboard/resources/icon.png --out "$build/Repoboard.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$build/Repoboard.iconset" -o "$build/Repoboard.icns"

version="$(uv run --quiet python -c 'import repoboard; print(repoboard.__version__)')"
uv sync --quiet --extra dev --group app --group visual
uv run --quiet pyinstaller --noconfirm --clean --windowed --log-level WARN \
    --name Repoboard \
    --icon "$build/Repoboard.icns" \
    --osx-bundle-identifier com.grigoriev.repoboard \
    --collect-data repoboard \
    --collect-submodules uvicorn \
    --collect-all desktop_notifier \
    --workpath "$build/work" --specpath "$build" --distpath "$root/dist" \
    packaging/repoboard_app.py

plist="$app/Contents/Info.plist"
# A menu bar agent: no Dock icon for the main process. The window process turns itself into a
# regular app, see repoboard/macos.py.
plist_set() {
    /usr/libexec/PlistBuddy -c "Set :$1 $3" "$plist" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :$1 $2 $3" "$plist"
}
plist_set LSUIElement bool true
plist_set CFBundleShortVersionString string "$version"
plist_set CFBundleVersion string "$version"
codesign --force --deep --sign - "$app"
echo "$app"
