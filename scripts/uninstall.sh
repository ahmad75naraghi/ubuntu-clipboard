#!/usr/bin/env bash
#
# uninstall.sh — remove the user level installation.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${UBUNTU_CLIPBOARD_VENV:-$HOME/.local/share/ubuntu-clipboard/venv}"
BIN_DIR="$HOME/.local/bin"

ASSUME_YES=0
PURGE=0

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --purge) PURGE=1; ASSUME_YES=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/uninstall.sh [options]

  -y, --yes   answer "yes" to every question
      --purge also delete the clipboard history and the configuration
  -h, --help  show this message
EOF
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

ask() {
  if [[ $ASSUME_YES -eq 1 || ! -t 0 ]]; then return 0; fi
  read -r -p "$1 [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]]
}

echo "Removing Ubuntu Clipboard"

# Stop the running instance first, otherwise it keeps serving the clipboard.
if [[ -x "$VENV_DIR/bin/ubuntu-clipboard" ]]; then
  "$VENV_DIR/bin/ubuntu-clipboard" --quit >/dev/null 2>&1 || true
fi

if [[ -x "$VENV_DIR/bin/ubuntu-clipboard" ]]; then
  if [[ $PURGE -eq 1 ]]; then
    "$VENV_DIR/bin/ubuntu-clipboard" --uninstall --purge || true
  else
    "$VENV_DIR/bin/ubuntu-clipboard" --uninstall || true
  fi
else
  echo "!  virtualenv not found, removing the files manually"
  DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
  CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
  CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
  for name in io.github.ahmad75naraghi.UbuntuClipboard ubuntu-clipboard ubuntu-clipboard-settings; do
    rm -f "$DATA_HOME/applications/$name.desktop"
  done
  for name in io.github.ahmad75naraghi.UbuntuClipboard ubuntu-clipboard ubuntu-clipboard-daemon; do
    rm -f "$CONFIG_HOME/autostart/$name.desktop"
  done
  rm -f "$DATA_HOME/icons/hicolor/512x512/apps/ubuntu-clipboard.png"
  rm -f "$CACHE_HOME/ubuntu-clipboard/ubuntu-clipboard.log"*
fi

rm -f "$BIN_DIR/ubuntu-clipboard" "$BIN_DIR/ubuntu-clipboard-daemon"
rm -rf "$VENV_DIR"

if [[ $PURGE -eq 0 ]]; then
  if ask "Also delete the clipboard history and settings?"; then
    PURGE=1
  fi
fi

if [[ $PURGE -eq 1 ]]; then
  DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
  CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
  rm -f "$DATA_HOME/ubuntu-clipboard/history.db" "$DATA_HOME/ubuntu-clipboard/history.db-wal" \
        "$DATA_HOME/ubuntu-clipboard/history.db-shm"
  rm -rf "$CONFIG_HOME/ubuntu-clipboard"
  echo "  ✓ history and settings deleted"
else
  echo "  history kept in ${XDG_DATA_HOME:-$HOME/.local/share}/ubuntu-clipboard"
fi

rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/ubuntu-clipboard"

# Remove a stale Win+V keybinding if the virtualenv was already gone.
if command -v gsettings >/dev/null 2>&1; then
  if gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null | grep -q ubuntu-clipboard; then
    python3 - <<'PY' || true
import ast
import subprocess

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEY = "custom-keybindings"
TARGET = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/"

raw = subprocess.run(["gsettings", "get", SCHEMA, KEY], capture_output=True, text=True).stdout.strip()
try:
    paths = ast.literal_eval(raw) if raw not in {"", "@as []"} else []
except (ValueError, SyntaxError):
    paths = []
paths = [path for path in paths if path != TARGET]
value = "@as []" if not paths else "[" + ", ".join(f"'{path}'" for path in paths) + "]"
subprocess.run(["gsettings", "set", SCHEMA, KEY, value], check=False)
print("  ✓ keybinding removed")
PY
  fi
fi

echo "Done. The python package itself was installed only inside $VENV_DIR, which is now gone."
echo "If you installed it elsewhere as well, remove it with: pip uninstall ubuntu-clipboard"
echo "repository: $REPO_DIR"
