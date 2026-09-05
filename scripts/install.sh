#!/usr/bin/env bash
# install.sh — نصب حرفه‌ای کلیپ‌بورد اوبونتو
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$HOME/.local/share/ubuntu-clipboard/venv"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"

echo "╔════════════════════════════════════════════════╗"
echo "║   Ubuntu Clipboard — نصب حرفه‌ای   Win+V     ║"
echo "╚════════════════════════════════════════════════╝"
echo ""

# 1. وابستگی‌های سیستمی
echo "📦 بررسی وابستگی‌ها..."
MISSING=()
check_pkg() { dpkg -l "$1" &>/dev/null || MISSING+=("$1"); }

# Python GI
if ! python3 -c "import gi" 2>/dev/null; then
  MISSING+=("python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1")
fi
for cmd in wl-copy xclip; do
  if ! command -v "$cmd" &>/dev/null; then
    if [[ "$cmd" == "wl-copy" ]]; then MISSING+=("wl-clipboard")
    else MISSING+=("xclip")
    fi
  fi
done

if [ ${#MISSING[@]} -ne 0 ]; then
  echo "  نصب وابستگی‌ها: ${MISSING[*]}"
  echo "  sudo apt update && sudo apt install -y ${MISSING[*]} wl-clipboard xclip xdotool wtype"
  if command -v apt &>/dev/null; then
    echo ""
    read -p "  آیا با sudo نصب کنم؟ [Y/n] " ans
    if [[ "$ans" != "n" && "$ans" != "N" ]]; then
      sudo apt update
      sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 wl-clipboard xclip xdotool  || true
      # wtype ممکن است در همه نسخه‌ها نباشد
      sudo apt install -y wtype  || echo "  ⚠️ wtype نصب نشد (اختیاری برای Wayland)"
    fi
  fi
else
  echo "  ✓ همه وابستگی‌ها موجود است"
fi

# 2. نصب پایتون
echo ""
echo "🐍 نصب پکیج پایتون..."
mkdir -p "$BIN_DIR" "$DESKTOP_DIR" "$AUTOSTART_DIR"
# سعی با pipx یا venv یا --user
if command -v pipx &>/dev/null; then
  pipx install "$REPO_DIR" --force  || pip install --user "$REPO_DIR"
else
  # venv اختصاصی برای ایزوله بودن
  if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
  fi
  "$VENV_DIR/bin/pip" install --upgrade pip
  "$VENV_DIR/bin/pip" install "$REPO_DIR"
  # wrapper
  cat > "$BIN_DIR/ubuntu-clipboard" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/ubuntu-clipboard" "\$@"
EOF
  cat > "$BIN_DIR/ubuntu-clipboard-daemon" <<EOF
#!/usr/bin/env bash
exec "$VENV_DIR/bin/ubuntu-clipboard-daemon" "\$@"
EOF
  chmod +x "$BIN_DIR/ubuntu-clipboard" "$BIN_DIR/ubuntu-clipboard-daemon"
  echo "  ✓ نصب در venv: $VENV_DIR"
fi

# اطمینان از PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo "  ⚠️  $BIN_DIR در PATH نیست — به .bashrc اضافه می‌شود"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
  export PATH="$HOME/.local/bin:$PATH"
fi

# 3. فایل‌های دسکتاپ + آیکون
echo ""
echo "🖥️  ثبت Autostart و میانبر..."
mkdir -p "$DESKTOP_DIR" "$AUTOSTART_DIR" "$HOME/.local/share/icons"
cp "$REPO_DIR/data/icons/ubuntu-clipboard.png" "$HOME/.local/share/icons/" 2>/dev/null || cp "$REPO_DIR/ubuntu_clipboard/assets/icon.png" "$HOME/.local/share/icons/ubuntu-clipboard.png" 2>/dev/null || true
gtk-update-icon-cache "$HOME/.local/share/icons" 2>/dev/null || true
mkdir -p "$DESKTOP_DIR" "$AUTOSTART_DIR"
cp "$REPO_DIR/data/ubuntu-clipboard.desktop" "$DESKTOP_DIR/"
cp "$REPO_DIR/data/ubuntu-clipboard.desktop" "$AUTOSTART_DIR/"
# به‌روزرسانی Exec اگر venv است
if [ -d "$VENV_DIR" ]; then
  sed -i "s|Exec=ubuntu-clipboard|Exec=$BIN_DIR/ubuntu-clipboard|" "$DESKTOP_DIR/ubuntu-clipboard.desktop"
  sed -i "s|Exec=ubuntu-clipboard|Exec=$BIN_DIR/ubuntu-clipboard|" "$AUTOSTART_DIR/ubuntu-clipboard.desktop"
fi
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

# 4. میانبر Win+V
echo ""
if command -v gsettings &>/dev/null; then
  bash "$REPO_DIR/scripts/setup-shortcut.sh" || echo "  ⚠️ ثبت میانبر ناموفق — دستی انجام دهید"
else
  echo "  ⚠️ gsettings یافت نشد — میانبر را دستی بسازید"
fi

# 5. تست
echo ""
echo "✅ نصب کامل شد!"
echo ""
echo "  اجرا:        ubuntu-clipboard"
echo "  دیمن:        ubuntu-clipboard-daemon"
echo "  میانبر:      Win+V (Super+V)"
echo "  تنظیمات:     داخل پنجره کلیپ‌بورد → ⚙️"
echo "  DB:          ~/.config/ubuntu-clipboard/history.db"
echo ""
echo "  برای اجرای فوری:"
echo "    ubuntu-clipboard &"
echo "  سپس Win+V را بزنید."
echo ""
# اجرا؟
read -p "  همین حالا اجرا کنم؟ [Y/n] " run
if [[ "$run" != "n" && "$run" != "N" ]]; then
  nohup ubuntu-clipboard --hidden >/tmp/ubuntu-clipboard.log 2>&1 &
  echo "  ✓ اجرا شد — لاگ: /tmp/ubuntu-clipboard.log"
  sleep 1
  echo "  Win+V را امتحان کنید!"
fi
