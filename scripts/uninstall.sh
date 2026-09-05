#!/usr/bin/env bash
set -e
echo "🗑️  حذف Ubuntu Clipboard..."

# kill
pkill -f ubuntu-clipboard || true

# pip
pip uninstall -y ubuntu-clipboard 2>/dev/null || pip3 uninstall -y ubuntu-clipboard 2>/dev/null || true
pipx uninstall ubuntu-clipboard 2>/dev/null || true
rm -rf "$HOME/.local/share/ubuntu-clipboard"

# desktop
rm -f "$HOME/.local/share/applications/ubuntu-clipboard.desktop"
rm -f "$HOME/.config/autostart/ubuntu-clipboard.desktop"

# shortcut - remove from gsettings
if command -v gsettings &>/dev/null; then
  SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
  KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/ubuntu-clipboard/"
  CURRENT=$(gsettings get $SCHEMA custom-keybindings 2>/dev/null || echo "[]")
  NEW=$(echo "$CURRENT" | sed "s#'${KEY_PATH}'##g" | sed "s#, ,#, #g" | sed "s#\[,#\[#g" | sed "s#, ]#]#g" | sed "s#\[ ]#[]#g")
  # simpler: if only ours, clear
  if [[ "$CURRENT" == *"ubuntu-clipboard"* ]]; then
    # if list contains only ours -> empty, else remove entry
    if [[ "$CURRENT" == "['$KEY_PATH']" ]]; then
      gsettings set $SCHEMA custom-keybindings "[]"
    else
      # crude but works
      gsettings set $SCHEMA custom-keybindings "$(echo $CURRENT | sed "s#'\/org\/gnome\/settings-daemon\/plugins\/media-keys\/custom-keybindings\/ubuntu-clipboard\/',\?##g" | sed "s#, ]#]#g" | sed "s#\[, #[#g")"
    fi
    echo "  ✓ میانبر حذف شد"
  fi
fi

echo "  برای حذف کامل تاریخچه:"
echo "    rm -rf ~/.config/ubuntu-clipboard"
read -p "  تاریخچه هم حذف شود؟ [y/N] " ans
if [[ "$ans" == "y" || "$ans" == "Y" ]]; then
  rm -rf "$HOME/.config/ubuntu-clipboard"
  echo "  ✓ تاریخچه حذف شد"
fi

echo "✅ حذف کامل شد."
