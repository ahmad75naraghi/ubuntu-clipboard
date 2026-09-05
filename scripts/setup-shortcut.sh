#!/usr/bin/env bash
# setup-shortcut.sh — ثبت میانبر Win+V در گنوم
set -e

APP_ID="ubuntu-clipboard"
KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/${APP_ID}/"
SCHEMA="org.gnome.settings-daemon.plugins.media-keys"

echo "⌨️  ثبت میانبر Win+V ..."

# پیدا کردن لیست فعلی
CURRENT=$(gsettings get $SCHEMA custom-keybindings 2>/dev/null || echo "@as []")
echo "  فعلی: $CURRENT"

# اگر قبلا هست، حذف نکن — اضافه کن
if [[ "$CURRENT" == *"$APP_ID"* ]]; then
  echo "  ✓ میانبر قبلا ثبت شده"
else
  # ساخت لیست جدید
  if [[ "$CURRENT" == "@as []" || "$CURRENT" == "[]" ]]; then
    NEW="['$KEY_PATH']"
  else
    # remove trailing ] and append
    NEW=$(echo "$CURRENT" | sed "s|]$|, '$KEY_PATH']|")
  fi
  gsettings set $SCHEMA custom-keybindings "$NEW"
  echo "  ✓ لیست به‌روز شد: $NEW"
fi

# تشخیص مسیر کامل اجرایی — مهم! چون Top Bar/shell بدون PATH اجرا می‌شود
BIN_CANDIDATES=(
  "$HOME/.local/bin/ubuntu-clipboard"
  "$HOME/.local/share/ubuntu-clipboard/venv/bin/ubuntu-clipboard"
  "/usr/local/bin/ubuntu-clipboard"
  "$(command -v ubuntu-clipboard 2>/dev/null || echo ubuntu-clipboard)"
)
BIN=""
for c in "${BIN_CANDIDATES[@]}"; do
  if [[ -x "$c" ]]; then BIN="$c"; break; fi
done
[[ -z "$BIN" ]] && BIN="ubuntu-clipboard"
# اگر venv باشد، همان را بگذار
if [[ -x "$HOME/.local/share/ubuntu-clipboard/venv/bin/ubuntu-clipboard" ]]; then
  BIN="$HOME/.local/share/ubuntu-clipboard/venv/bin/ubuntu-clipboard"
elif [[ -x "$HOME/.local/bin/ubuntu-clipboard" ]]; then
  BIN="$HOME/.local/bin/ubuntu-clipboard"
fi

# تنظیم مقادیر کلید سفارشی — از مسیر کامل استفاده می‌کنیم تا در Wayland هم کار کند
# ——— بررسی custom1 تکراری ———
# اگر custom1 همین Super+v و command ubuntu-clipboard دارد، تکراری است — پاکش می‌کنیم تا تداخل حل شود
C1_CMD=$(gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ command 2>/dev/null || echo "")
C1_BIND=$(gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ binding 2>/dev/null || echo "")
C0_CMD=$(gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/ command 2>/dev/null || echo "")
# اگر custom1 تکراری ubuntu-clipboard است، حذف از لیست و reset
if [[ "$C1_CMD" == *"ubuntu-clipboard"* ]]; then
  echo "  🧹 custom1 تکراری تشخیص داده شد ($C1_CMD) — حذف از لیست"
  # لیست فعلی را بدون custom1 بازسازی
  CURR=$(gsettings get $SCHEMA custom-keybindings 2>/dev/null)
  # حذف custom1 از لیست
  NEW2=$(echo "$CURR" | sed "s|'/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/'||" | sed "s|, ,|, |" | sed "s|\[, |[|" | sed "s|, ]|]|" | sed "s|,,|,|g")
  # اگر NEW2 خالی شد، بازسازی با ubuntu-clipboard
  if [[ "$NEW2" == "@as []" || "$NEW2" == "[]" ]]; then
    NEW2="['$KEY_PATH']"
  else
    # اطمینان ubuntu-clipboard در لیست هست
    if [[ "$NEW2" != *"ubuntu-clipboard"* ]]; then
      NEW2=$(echo "$NEW2" | sed "s|]$|, '$KEY_PATH']|")
    fi
  fi
  gsettings set $SCHEMA custom-keybindings "$NEW2"
  echo "  ✓ لیست جدید: $NEW2"
  # reset custom1
  gsettings reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ name 2>/dev/null || true
  gsettings reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ command 2>/dev/null || true
  gsettings reset org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/ binding 2>/dev/null || true
fi

gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"$KEY_PATH" name 'Clipboard — Win+V'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"$KEY_PATH" command "$BIN --toggle"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"$KEY_PATH" binding '<Super>v'
echo "  ✓ Binding تنظیم شد به <Super>v (Win+V)"
# نمایش وضعیت grab
sleep 0.5
journalctl --user --since "10 seconds ago" 2>&1 | grep -i "grab accelerator" | head -n 5 || echo "  (no grab error — میانبر موفق)"

echo "✓ میانبر Win+V ثبت شد!"
echo "  Command: ubuntu-clipboard --toggle"
echo "  Binding: Super+v"
echo ""
echo "  برای تست: کلید Win+V را بزنید (Super+V)"
echo "  برای تغییر: Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts"
