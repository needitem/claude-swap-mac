#!/bin/bash
# Build "Claude Swap.app" — a menu bar + dashboard wrapper around cswap.
#
#   ./build_app.sh                 -> /Applications/Claude Swap.app
#   ./build_app.sh ~/Applications  -> ~/Applications/Claude Swap.app
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/Applications}"
APP="$DEST/Claude Swap.app"
BUILD="$REPO/build"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

echo "==> icon"
rm -rf "$BUILD"; mkdir -p "$BUILD/ClaudeSwap.iconset"
python3 "$REPO/assets/mkicon.py" "$BUILD/icon.png"
for spec in "16 icon_16x16" "32 icon_16x16@2x" "32 icon_32x32" "64 icon_32x32@2x" \
            "128 icon_128x128" "256 icon_128x128@2x" "256 icon_256x256" \
            "512 icon_256x256@2x" "512 icon_512x512" "1024 icon_512x512@2x"; do
  size="${spec%% *}"; name="${spec##* }"
  sips -z "$size" "$size" "$BUILD/icon.png" --out "$BUILD/ClaudeSwap.iconset/$name.png" >/dev/null
done
iconutil -c icns "$BUILD/ClaudeSwap.iconset" -o "$BUILD/AppIcon.icns"

echo "==> bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleName</key><string>Claude Swap</string>
	<key>CFBundleDisplayName</key><string>Claude Swap</string>
	<key>CFBundleExecutable</key><string>ClaudeSwap</string>
	<key>CFBundleIdentifier</key><string>com.claude-swap.menubar</string>
	<key>CFBundleIconFile</key><string>AppIcon</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleShortVersionString</key><string>0.1.0</string>
	<key>CFBundleVersion</key><string>0.1.0</string>
	<key>LSMinimumSystemVersion</key><string>12.0</string>
	<key>LSUIElement</key><true/>
	<key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/ClaudeSwap" <<'LAUNCHER'
#!/bin/zsh
# Launcher for the claude-swap dashboard. See README "How the .app works".

LOG_DIR="$HOME/.claude-swap-backup"
mkdir -p "$LOG_DIR" 2>/dev/null
LOG="$LOG_DIR/menubar-app.log"
[[ -f "$LOG" && $(stat -f%z "$LOG" 2>/dev/null || echo 0) -gt 1048576 ]] && : >"$LOG"
# Under LaunchServices there is no terminal for a startup failure to land in.
exec >>"$LOG" 2>&1
print -r -- "--- launch $(date '+%Y-%m-%d %H:%M:%S') pid=$$ ---"

APP_BIN=""
for candidate in \
  "$HOME/.local/bin/cswap-dashboard" \
  "/opt/homebrew/bin/cswap-dashboard" \
  "/usr/local/bin/cswap-dashboard"
do
  [[ -x "$candidate" ]] && { APP_BIN="$candidate"; break }
done
if [[ -z "$APP_BIN" ]]; then
  APP_BIN=$(PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" \
            command -v cswap-dashboard 2>/dev/null)
fi
if [[ -z "$APP_BIN" ]]; then
  print -r -- "cswap-dashboard not found"
  osascript -e 'display alert "Claude Swap is not installed" message "Run:\n\n    uv tool install cswap-dashboard" as critical'
  exit 1
fi

# Single instance. The pattern is anchored to the *whole* argv of the real GUI
# process so an unrelated process merely mentioning these words on its command
# line — a shell tailing this very log, say — cannot look like a running copy.
RUNNING=$(pgrep -f -U "$(id -u)" '^[^ ]*/python[0-9.]* [^ ]*/cswap-dashboard$' 2>/dev/null)
if [[ -n "$RUNNING" ]]; then
  print -r -- "already running (pid $RUNNING) — raising it"
  # Nothing to raise programmatically from here; the menu bar item is the entry
  # point, and a second status item would only confuse.
  exit 0
fi

# Do NOT exec, and do not leave it as this process's child.
#
# The binary that runs the GUI is the uv/pipx venv's python, which lives
# outside this bundle. If it inherits the bundle's LaunchServices registration,
# macOS 26 accepts its NSStatusItem over XPC and then never draws it: the menu
# bar item silently does not appear, with nothing in any log. Unsetting
# __CFBundleIdentifier is not enough — LaunchServices makes the association at
# exec time, not through the environment. Orphaning the process makes it
# register with the WindowServer on its own, exactly as running the command in
# a terminal does. Measured on macOS 26.5.1: exec'd -> no icon; orphaned -> icon.
unset __CFBundleIdentifier
"$APP_BIN" &
disown
print -r -- "started detached (pid $!)"
exit 0
LAUNCHER

chmod +x "$APP/Contents/MacOS/ClaudeSwap"

echo "==> sign (ad-hoc)"
codesign --force --sign - "$APP"

rm -rf "$BUILD"
echo "==> built: $APP"
