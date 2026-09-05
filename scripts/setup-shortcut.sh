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
    NEW=$(echo "$CURRENT" | sed "s/]$/, '$KEY_PATH']/")
  fi
  gsettings set $SCHEMA custom-keybindings "$NEW"
  echo "  ✓ لیست به‌روز شد: $NEW"
fi

# تنظیم مقادیر کلید سفارشی
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"$KEY_PATH" name 'Clipboard — Win+V'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"$KEY_PATH" command 'ubuntu-clipboard --toggle'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"$KEY_PATH" binding '<Super>v'

echo "✓ میانبر Win+V ثبت شد!"
echo "  Command: ubuntu-clipboard --toggle"
echo "  Binding: Super+v"
echo ""
echo "  برای تست: کلید Win+V را بزنید (Super+V)"
echo "  برای تغییر: Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts"
