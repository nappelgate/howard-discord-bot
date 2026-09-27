#!/usr/bin/env bash
#
# Discord Chaos Bot — macOS auto-setup (native, launchd).
# Idempotent: safe to run more than once. Installs Homebrew (if needed),
# Python + ffmpeg, the bot's dependencies, and a launchd service that keeps
# the bot running and restarts it on crash / login.
#
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
echo "==> Project directory: $DIR"

# --- 1. Pre-flight --------------------------------------------------------
if [ ! -f .env ]; then
  echo "ERROR: no .env file found in $DIR. Copy your .env over first." >&2
  exit 1
fi
if ! grep -Eq '^DISCORD_TOKEN=.+' .env; then
  echo "ERROR: .env has no DISCORD_TOKEN value. Fill it in, then re-run." >&2
  exit 1
fi

# A Windows .venv may have hitched a ride during transfer — it's useless here.
rm -rf .venv __pycache__ cogs/__pycache__ 2>/dev/null || true

# --- 2. Homebrew ----------------------------------------------------------
if ! command -v brew >/dev/null 2>&1; then
  echo "==> Homebrew not found. Installing (you may be asked for your Mac password)…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# Load brew into this shell (Apple Silicon -> /opt/homebrew, Intel -> /usr/local)
if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"
fi
BREW_PREFIX="$(brew --prefix)"
echo "==> Homebrew prefix: $BREW_PREFIX"

# --- 3. Python + ffmpeg ---------------------------------------------------
echo "==> Installing python + ffmpeg via Homebrew…"
brew install python ffmpeg

PY="$BREW_PREFIX/bin/python3"
FFMPEG="$BREW_PREFIX/bin/ffmpeg"
"$FFMPEG" -version | head -n 1

# --- 4. Virtual environment + dependencies --------------------------------
echo "==> Creating virtual environment and installing dependencies…"
"$PY" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

# --- 5. Point FFMPEG_PATH at the brew ffmpeg (replace any Windows path) ----
if grep -q '^FFMPEG_PATH=' .env; then
  sed -i '' "s|^FFMPEG_PATH=.*|FFMPEG_PATH=$FFMPEG|" .env
else
  printf '\nFFMPEG_PATH=%s\n' "$FFMPEG" >> .env
fi
echo "==> FFMPEG_PATH set to $FFMPEG"

# --- 6. launchd service ---------------------------------------------------
LABEL="com.chaosbot.discord"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$DIR/.venv/bin/python</string>
        <string>$DIR/bot.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$DIR/chaosbot.log</string>
    <key>StandardErrorPath</key>
    <string>$DIR/chaosbot.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$BREW_PREFIX/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLISTEOF
echo "==> Wrote launchd service: $PLIST"

# --- 7. (Re)load the service ---------------------------------------------
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
  || { launchctl unload "$PLIST" 2>/dev/null || true; launchctl load -w "$PLIST"; }
echo "==> Service loaded. Giving the bot a few seconds to connect…"

sleep 6
echo "----------------------------------------------------------------------"
tail -n 25 "$DIR/chaosbot.log" 2>/dev/null || echo "(no log yet)"
echo "----------------------------------------------------------------------"
if grep -q "Logged in as" "$DIR/chaosbot.log" 2>/dev/null; then
  echo "SUCCESS: the bot connected to Discord and is now running under launchd."
else
  echo "NOTE: didn't see 'Logged in as' yet — check $DIR/chaosbot.log in a moment."
fi
