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
# AppIndicator برای آیکون تسک‌بار
if ! dpkg -l gir1.2-ayatanaappindicator3-0.1 &>/dev/null && ! dpkg -l gir1.2-appindicator3-0.1 &>/dev/null; then
  MISSING+=("gir1.2-ayatanaappindicator3-0.1")
fi
if ! dpkg -l gnome-shell-extension-appindicator &>/dev/null; then
  MISSING+=("gnome-shell-extension-appindicator")
fi

if [ ${#MISSING[@]} -ne 0 ]; then
  echo "  نصب وابستگی‌ها: ${MISSING[*]}"
  echo "  sudo apt update && sudo apt install -y ${MISSING[*]} wl-clipboard xclip xdotool wtype"
  if command -v apt &>/dev/null; then
    echo ""
    # اگر در محیط غیرتعاملی هستیم (CI) خودکار Y
    if [[ ! -t 0 ]]; then ans="Y"; else read -p "  آیا با sudo نصب کنم؟ [Y/n] " ans; fi
    if [[ "$ans" != "n" && "$ans" != "N" ]]; then
      sudo apt update
      sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 wl-clipboard xclip xdotool gir1.2-ayatanaappindicator3-0.1 gnome-shell-extension-appindicator || true
      sudo apt install -y gir1.2-appindicator3-0.1  || true
      sudo apt install -y wtype  || echo "  ⚠️ wtype نصب نشد (اختیاری برای Wayland)"
      # فعال‌سازی افزونه AppIndicator برای دیدن آیکون در Top Bar
      gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com 2>/dev/null || gnome-extensions enable appindicator@rgcjonas.gmail.com 2>/dev/null || true
    fi
  fi
else
  echo "  ✓ همه وابستگی‌ها موجود است"
fi

# 2. نصب پایتون
echo ""
echo "🐍 نصب پکیج پایتون..."
mkdir -p "$BIN_DIR" "$DESKTOP_DIR" "$AUTOSTART_DIR"
# چک tk
if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "  ⚠️ python3-tk نصب نیست — برای fallback لازم است"
  MISSING_TK=1
else
  MISSING_TK=0
fi
if [[ $MISSING_TK -eq 1 ]]; then
  echo "  تلاش برای نصب python3-tk ..."
  sudo apt install -y python3-tk 2>/dev/null || echo "  ⚠️ نصب tk ناموفق — ادامه می‌دهیم (GTK اصلی استفاده می‌شود)"
fi
# سعی با pipx یا venv یا --user
if command -v pipx &>/dev/null; then
  pipx install "$REPO_DIR" --force  || pip install --user "$REPO_DIR"
else
  # venv اختصاصی — حتما با --system-site-packages تا python3-gi دیده شود!
  if [ -d "$VENV_DIR" ] && ! "$VENV_DIR/bin/python" -c "import gi" 2>/dev/null; then
    echo "  ♻️  venv قدیمی بدون دسترسی به gi — بازسازی با --system-site-packages ..."
    rm -rf "$VENV_DIR"
  fi
  if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv --system-site-packages "$VENV_DIR" || python3 -m venv "$VENV_DIR"
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
  echo "  ✓ نصب در venv: $VENV_DIR (system-site-packages)"
  # تست gi داخل venv
  if ! "$VENV_DIR/bin/python" -c "import gi; gi.require_version('Gtk','4.0')" 2>/dev/null; then
    echo "  ⚠️  هشدار: gi داخل venv دیده نمی‌شود!"
    echo "     سعی کنید: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
  else
    echo "  ✓ GTK داخل venv تایید شد"
  fi
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
# settings launcher هم
cp "$REPO_DIR/data/ubuntu-clipboard-settings.desktop" "$DESKTOP_DIR/" 2>/dev/null || true
# به‌روزرسانی Exec اگر venv است
if [ -d "$VENV_DIR" ]; then
  sed -i "s|Exec=ubuntu-clipboard|Exec=$BIN_DIR/ubuntu-clipboard|" "$DESKTOP_DIR/ubuntu-clipboard.desktop"
  sed -i "s|Exec=ubuntu-clipboard|Exec=$BIN_DIR/ubuntu-clipboard|" "$AUTOSTART_DIR/ubuntu-clipboard.desktop"
  sed -i "s|Exec=ubuntu-clipboard|Exec=$BIN_DIR/ubuntu-clipboard|" "$DESKTOP_DIR/ubuntu-clipboard-settings.desktop" 2>/dev/null || true
fi
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

# 4. میانبر Win+V
echo ""
if command -v gsettings &>/dev/null; then
  bash "$REPO_DIR/scripts/setup-shortcut.sh" || echo "  ⚠️ ثبت میانبر ناموفق — دستی انجام دهید"
else
  echo "  ⚠️ gsettings یافت نشد — میانبر را دستی بسازید"
fi

# 5. تست و اجرا
# kill old
pkill -f ubuntu-clipboard 2>/dev/null || true
sleep 0.5

echo ""
echo "✅ نصب کامل شد!"
echo ""
echo "  اجرا:        ubuntu-clipboard              (پنجره باز می‌شود)"
echo "  پس‌زمینه:    ubuntu-clipboard --hidden     (با آیکون تسک‌بار)"
echo "  میانبر:      Win+V (Super+V)"
echo "  تنظیمات:     ubuntu-clipboard --settings   یا آیکون Top Bar → ⚙️ تنظیمات"
echo "  وضعیت:       ubuntu-clipboard --status"
echo "  لاگ:         cat /tmp/ubuntu-clipboard.log"
echo "  DB:          ~/.config/ubuntu-clipboard/history.db"
echo ""
echo "  آیکون تسک‌بار: در Top Bar کنار ساعت/وای‌فای ببینید — کلیک = باز شدن کلیپ‌بورد"
echo "  اگر آیکون را نمی‌بینید:"
echo "    sudo apt install gnome-shell-extension-appindicator"
echo "    gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com"
echo "    سپس لاگ‌اوت/لاگین کنید"
echo ""
# اجرا — همیشه با لاگ
echo "🚀 اجرای برنامه..."
if [[ ! -t 0 ]]; then run="Y"; else read -p "  همین حالا اجرا کنم؟ [Y/n] " run; fi
if [[ "$run" != "n" && "$run" != "N" ]]; then
  # اجرای مخفی با آیکون تسک‌بار
  nohup "$BIN_DIR/ubuntu-clipboard" --hidden >/tmp/ubuntu-clipboard.log 2>&1 &
  sleep 1.2
  if pgrep -f ubuntu-clipboard >/dev/null; then
    echo "  ✓ اجرا شد — PID: $(pgrep -f ubuntu-clipboard | head -1)"
    echo "  ✓ لاگ: /tmp/ubuntu-clipboard.log"
    echo ""
    echo "  👉 حالا تست کنید:"
    echo "     1) یک متن کپی کنید (Ctrl+C)"
    echo "     2) روی آیکون کلیپ‌بورد در Top Bar کلیک کنید"
    echo "     3) یا Win+V بزنید"
    echo "     4) ubuntu-clipboard --status  برای دیدن وضعیت"
    echo "     5) ubuntu-clipboard --settings برای تنظیمات"
  else
    echo "  ✗ اجرا نشد — لاگ:"
    cat /tmp/ubuntu-clipboard.log 2>/dev/null || echo "    (لاگ خالی)"
    echo "  سعی کنید دستی: ubuntu-clipboard --debug"
  fi
fi
echo ""
echo "  برای عیب‌یابی: ubuntu-clipboard --status"
echo "  برای تنظیمات: ubuntu-clipboard --settings"
