#!/usr/bin/env bash
#
# install.sh — install Ubuntu Clipboard for the current user.
#
# The script only installs distribution packages, then hands over to
# `ubuntu-clipboard --install`, which writes the desktop entry, the icon, the
# autostart file and the Win+V keybinding. Re-running it is safe.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${UBUNTU_CLIPBOARD_VENV:-$HOME/.local/share/ubuntu-clipboard/venv}"
BIN_DIR="$HOME/.local/bin"

APT_PACKAGES=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1)
OPTIONAL_PACKAGES=(wl-clipboard xclip xdotool wtype ydotool)

ASSUME_YES=0
SKIP_APT=0
START_NOW=1

for arg in "$@"; do
  case "$arg" in
    -y|--yes) ASSUME_YES=1 ;;
    --no-apt) SKIP_APT=1 ;;
    --no-start) START_NOW=0 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/install.sh [options]

  -y, --yes       answer "yes" to every question
      --no-apt    never call apt-get (do not install distribution packages)
      --no-start  do not start the background service at the end
  -h, --help      show this message
EOF
      exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
warn() { printf '!  %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

ask() {
  local prompt="$1"
  if [[ $ASSUME_YES -eq 1 || ! -t 0 ]]; then return 0; fi
  read -r -p "$prompt [Y/n] " reply
  [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

say "Ubuntu Clipboard — user installation"
say "repository: $REPO_DIR"

# ── 1. distribution packages ────────────────────────────────────────────────
gtk_ok() { python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: F401
PY
}

missing=()
gtk_ok || missing+=("${APT_PACKAGES[@]}")
for pkg in "${OPTIONAL_PACKAGES[@]}"; do
  case "$pkg" in
    wl-clipboard) have wl-copy || missing+=("$pkg") ;;
    xclip)        have xclip    || missing+=("$pkg") ;;
    xdotool)      have xdotool  || missing+=("$pkg") ;;
    # wtype and ydotool are not packaged everywhere; they are suggestions only.
  esac
done

if [[ ${#missing[@]} -gt 0 && $SKIP_APT -eq 0 ]] && have apt-get; then
  # De-duplicate while keeping the order.
  unique=()
  for pkg in "${missing[@]}"; do
    [[ " ${unique[*]} " == *" $pkg "* ]] || unique+=("$pkg")
  done
  say "missing packages: ${unique[*]}"
  if ask "Install them with apt-get (needs sudo)?"; then
    sudo apt-get update -qq || warn "apt-get update failed, continuing"
    sudo apt-get install -y "${unique[@]}" || warn "some packages could not be installed"
  else
    warn "skipping apt — the application may not start"
  fi
elif [[ ${#missing[@]} -gt 0 ]]; then
  warn "missing packages: ${missing[*]}"
fi

if ! gtk_ok; then
  warn "GTK 4 bindings are still missing; install python3-gi and gir1.2-gtk-4.0"
fi

# ── 2. python package inside an isolated virtualenv ────────────────────────
say "installing the python package into $VENV_DIR"
mkdir -p "$VENV_DIR" "$BIN_DIR"

if ! python3 -m venv --help >/dev/null 2>&1; then
  warn "the 'venv' module is unavailable — install it with:"
  warn "  sudo apt install python3-venv"
  exit 1
fi

# PEP 668 (Ubuntu 23.04+) forbids `pip install` outside a virtualenv, hence the
# --system-site-packages venv: it keeps PyGObject visible to the package.
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  rm -rf "$VENV_DIR"
  python3 -m venv --system-site-packages "$VENV_DIR"
elif ! "$VENV_DIR/bin/python" -c "import gi" >/dev/null 2>&1; then
  say "recreating the virtualenv without isolation from system packages"
  rm -rf "$VENV_DIR"
  python3 -m venv --system-site-packages "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade "$REPO_DIR"

for name in ubuntu-clipboard ubuntu-clipboard-daemon; do
  cat > "$BIN_DIR/$name" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/$name" "\$@"
EOF
  chmod 0755 "$BIN_DIR/$name"
done
say "installed: $VENV_DIR/bin/ubuntu-clipboard"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not in PATH — add it to ~/.profile:"
     warn "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ── 3. desktop integration ─────────────────────────────────────────────────
say "registering the desktop entry, icon, autostart and the Win+V shortcut"
"$VENV_DIR/bin/ubuntu-clipboard" --install || warn "desktop integration reported problems"

# ── 4. start the service ───────────────────────────────────────────────────
if [[ $START_NOW -eq 1 ]]; then
  "$VENV_DIR/bin/ubuntu-clipboard" --quit >/dev/null 2>&1 || true
  nohup "$VENV_DIR/bin/ubuntu-clipboard" --background >/dev/null 2>&1 &
  sleep 1
  if "$VENV_DIR/bin/ubuntu-clipboard" --status | grep -q "instance *running"; then
    say "service started"
  else
    warn "the service does not seem to be running — check 'ubuntu-clipboard --logs'"
  fi
fi

cat <<'EOF'

Done.

  Open the window     Win+V  (or: ubuntu-clipboard --toggle)
  Preferences         ubuntu-clipboard --settings
  Status              ubuntu-clipboard --status
  Logs                ubuntu-clipboard --logs
  Uninstall           ./scripts/uninstall.sh

If Win+V does nothing, log out and back in once so GNOME picks up the new
keybinding, or check that no other shortcut uses <Super>v.
EOF
