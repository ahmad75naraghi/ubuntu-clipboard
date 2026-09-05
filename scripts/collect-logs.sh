#!/usr/bin/env bash
# collect-logs.sh — جمع‌آوری لاگ کامل برای عیب‌یابی چشمک
set -e

OUT="/tmp/ubuntu-clipboard-collect-$(date +%Y%m%d-%H%M%S).txt"
echo "📋 جمع‌آوری لاگ‌ها به $OUT ..."

{
echo "===== Ubuntu Clipboard — Collect Logs ====="
date
echo ""
echo "===== ps aux | grep clipboard ====="
ps aux | grep -i clipboard || echo "(none)"
echo ""
echo "===== pgrep -af ubuntu-clipboard ====="
pgrep -af ubuntu-clipboard || echo "(none)"
echo ""
echo "===== window lock ====="
ls -l ~/.cache/ubuntu-clipboard/window.pid 2>&1 || echo "no lock"
cat ~/.cache/ubuntu-clipboard/window.pid 2>&1 || echo "no lock content"
ls -l ~/.cache/ubuntu-clipboard/toggle.debounce 2>&1 || echo "no debounce"
cat ~/.cache/ubuntu-clipboard/toggle.debounce 2>&1 || echo "no debounce"
echo ""
echo "===== gsettings ====="
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>&1 | head -n 5
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/ command 2>&1
gsettings get org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/ binding 2>&1
echo ""
echo "===== autostart ====="
ls -l ~/.config/autostart/ubuntu-clipboard*.desktop 2>&1
cat ~/.config/autostart/ubuntu-clipboard-daemon.desktop 2>&1 | head -n 20
echo ""
echo "===== history count ====="
python3 -c "from ubuntu_clipboard.history import HistoryManager; hm=HistoryManager(); print('count',hm.count())" 2>&1
echo ""
echo "===== CACHE LOG (last 150 lines) ====="
cat ~/.cache/ubuntu-clipboard/debug.log 2>&1 | tail -n 150 || echo "no cache log"
echo ""
echo "===== TMP LOG ====="
cat /tmp/ubuntu-clipboard.log 2>&1 | tail -n 100 || echo "no tmp log"
echo ""
echo "===== journal (last 50) ====="
journalctl --user --since "5 minutes ago" 2>&1 | grep -i clipboard | tail -n 50 || echo "no journal"
} | tee "$OUT"

echo ""
echo "✅ لاگ جمع شد: $OUT"
echo "   لطفاً این فایل را برای بررسی بفرستید:"
echo "   cat $OUT"
echo "   یا: xclip -selection clipboard < $OUT"
