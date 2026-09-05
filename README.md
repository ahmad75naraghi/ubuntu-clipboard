# 📋 Ubuntu Clipboard — کلیپ‌بورد حرفه‌ای شبیه ویندوز 11 برای اوبونتو

> **Win+V** روی اوبونتو — دقیقا مثل ویندوز 11، زیبا، سریع و کامل

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Ubuntu](https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04%20%7C%2024.10-E95420)
![GNOME](https://img.shields.io/badge/GNOME-Wayland%20%26%20X11-4A86CF)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ✨ نمای کلی

این پروژه یک **کلیپ‌بورد کامل و حرفه‌ای** برای اوبونتو است که تمام قابلیت‌های کلیپ‌بورد ویندوز 11 را با همان تجربه کاربری پیاده‌سازی می‌کند:

- پنجره شناور شیشه‌ای (Acrylic) وسط صفحه با گوشه‌های گرد
- تاریخچه 80+ آیتم با **جستجوی فوری**
- سنجاق (📌 Pin)، حذف، پاک کردن همه
- پشتیبانی **متن، کد، لینک، تصویر، فایل و رنگ**
- **کلیک برای Paste** — مثل ویندوز، خودکار Ctrl+V می‌زند
- اجرای پس‌زمینه (Daemon) + Autostart
- **Win+V (Super+V)** — دقیقا همان میانبر ویندوز
- سازگار با **Wayland و X11**، گنوم 42+، اوبونتو 22.04 تا 24.10
- تم تاریک/روشن/سیستمی، تنظیمات کامل

<p align="center">
  <img src="ubuntu_clipboard/assets/preview.png" alt="Ubuntu Clipboard Preview" width="720" style="border-radius:14px; box-shadow:0 12px 40px rgba(0,0,0,0.25)"/>
  <br/>
  <em>طراحی الهام‌گرفته از Windows 11 — Acrylic + Mica</em>
</p>

---

## 🎯 قابلیت‌ها — دقیقاً مثل ویندوز

| قابلیت ویندوز | وضعیت | توضیح |
|---|---|---|
| `Win+V` برای باز کردن | ✅ | میانبر سیستمی گنوم، قابل تغییر |
| تاریخچه متنی | ✅ | تا 80 آیتم (قابل تنظیم تا 300) |
| تصاویر | ✅ | PNG/JPG از کلیپ‌بورد، thumbnail |
| سنجاق کردن | ✅ | تا 20 آیتم سنجاق، همیشه بالا |
| جستجو | ✅ | فیلتر زنده داخل پنجره |
| حذف تکی / پاک کردن همه | ✅ | با نگه‌داشتن سنجاق‌ها |
| کلیک برای Paste | ✅ | کپی به کلیپ‌بورد + شبیه‌سازی Ctrl+V |
| پیش‌نمایش رنگ و لینک | ✅ | تشخیص خودکار `#ff5500` و `https://` |
| تشخیص کد | ✅ | هایلایت خودکار Python/JS/SQL/... |
| فایل‌ها | ✅ | لیست URI فایل‌های کپی‌شده |
| نادیده گرفتن رمزها | ✅ | Regex حساس، OTP و کارت بانکی |
| Autostart | ✅ | اجرای خودکار پس از لاگین |
| Wayland + X11 | ✅ | `wl-clipboard` / `xclip` / `wtype` / `xdotool` |
| کیبورد کامل | ✅ | `↑↓` حرکت، `Enter` Paste، `Del` حذف، `Ctrl+P` سنجاق، `Esc` بستن |

---

## 🚀 نصب سریع (30 ثانیه)

### روش 1 — اسکریپت خودکار (پیشنهادی)

```bash
git clone https://github.com/ahmad75naraghi/ubuntu-clipboard.git
cd ubuntu-clipboard
chmod +x scripts/install.sh
./scripts/install.sh
# سپس Win+V را بزنید!
```

اسکریپت به‌صورت خودکار:
1. وابستگی‌ها را نصب می‌کند (`python3-gi`, `gir1.2-adw-1`, `wl-clipboard`, `xclip`, `xdotool`)
2. پکیج را در `~/.local` نصب می‌کند
3. Autostart می‌سازد
4. میانبر **Super+V** را ثبت می‌کند

### روش 2 — دستی

```bash
sudo apt update
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 wl-clipboard xclip xdotool wtype -y

pip install --user .

# اجرای دستی
ubuntu-clipboard --hidden &

# ثبت میانبر (یا از Settings → Keyboard انجام دهید)
./scripts/setup-shortcut.sh
```

> **نکته Wayland:** در اوبونتو 22.04+ پیش‌فرض Wayland است. برای Paste خودکار، `wtype` یا `ydotool` نیاز است. اگر `wtype` در مخازن نبود، `xdotool` روی XWayland هم کار می‌کند، در غیر این‌صورت فقط Copy می‌شود و شما `Ctrl+V` می‌زنید.

---

## 🎮 نحوه استفاده

1. هر چیزی را **کپی** کنید (`Ctrl+C` یا کلیک راست)
2. کلید **Win+V** (یا `Super+V`) را بزنید — پنجره شیشه‌ای باز می‌شود
3. **جستجو** کنید یا اسکرول کنید
4. روی آیتم **کلیک** کنید — خودکار در برنامه قبلی Paste می‌شود
5. برای سنجاق: دکمه 📌 یا `Ctrl+P`
6. برای حذف: 🗑️ یا `Del`

### میانبرهای کیبورد داخل پنجره

| کلید | عمل |
|---|---|
| `Esc` | بستن |
| `↑` `↓` | حرکت |
| `Enter` | Paste آیتم انتخاب‌شده |
| `Ctrl+1..9` | Paste سریع آیتم 1 تا 9 |
| `Del` | حذف |
| `Ctrl+P` | سنجاق/برداشتن |

---

## ⚙️ تنظیمات

داخل پنجره کلیپ‌بورد روی **⚙️** بزنید، یا فایل را ویرایش کنید:

```json
// ~/.config/ubuntu-clipboard/config.json
{
  "max_items": 80,
  "pin_limit": 20,
  "theme": "dark",
  "window_width": 420,
  "window_height": 560,
  "keep_pinned_on_clear": true,
  "exclude_sensitive": true
}
```

| گزینه | توضیح |
|---|---|
| `max_items` | حداکثر تاریخچه (20–300) |
| `theme` | `dark` / `light` / `system` |
| `exclude_apps` | عدم ذخیره از اپ‌های حساس (Keepass...) |
| `ignore_regex` | Regex برای نادیده گرفتن (رمزها) |

---

## 🏗️ معماری فنی

```
ubuntu-clipboard/
├── ubuntu_clipboard/
│   ├── app.py              # GtkApplication + D-Bus toggle
│   ├── daemon.py           # مانیتور کلیپ‌بورد (poll + GDK signal)
│   ├── history.py          # SQLite — تشخیص نوع، dedup، pin
│   ├── clipboard.py        # abstraction Wayland/X11 (wl-paste/xclip/GDK)
│   ├── paste.py            # شبیه‌سازی Ctrl+V (wtype/xdotool/ydotool)
│   ├── config.py           # JSON config + ignore rules
│   └── ui/
│       ├── window.py       # پنجره اصلی — GTK4/Adw + Tkinter fallback
│       ├── settings.py     # دیالوگ تنظیمات
│       └── styles.css      # تم Acrylic ویندوز 11
├── data/*.desktop          # Autostart
└── scripts/*.sh            # install / shortcut
```

**جریان داده:**

```
[Ctrl+C in any app] → GDK clipboard changed / poll wl-paste → HistoryManager.add() → SQLite
Win+V → D-Bus app.toggle → Window.present() → list(pinned+recent) → Click → write_text() → simulate_paste()
```

- **Wayland:** `wl-paste` / `wl-copy` + `wtype` برای Paste
- **X11:** `xclip` / `xsel` + `xdotool`
- ذخیره: `~/.config/ubuntu-clipboard/history.db` (SQLite, hash dedup)

---

## 🔧 عیب‌یابی

**Win+V کار نمی‌کند؟**
```bash
# بررسی
gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings
# ثبت مجدد
./scripts/setup-shortcut.sh
# یا دستی: Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts → Add
# Name: Clipboard  Command: ubuntu-clipboard --toggle  Shortcut: Super+V
```

**Paste خودکار نمی‌شود (Wayland)؟**
```bash
sudo apt install wtype ydotool
# ydotool نیاز به سرویس دارد:
sudo systemctl enable --now ydotool
# در غیر این‌صورت فقط Copy می‌شود — خودتان Ctrl+V بزنید
```

**پنجره باز نمی‌شود؟**
```bash
cat /tmp/ubuntu-clipboard.log
ubuntu-clipboard  # اجرای فورگراند برای دیدن خطا
```

**GTK4 ندارم؟**
- برنامه خودکار به **Tkinter fallback** می‌رود (ظاهر مشابه، بدون نیاز به GI)
- برای تجربه کامل: `sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`

---

## 🗑️ حذف

```bash
./scripts/uninstall.sh
# یا دستی
pip uninstall ubuntu-clipboard
rm -rf ~/.config/ubuntu-clipboard
```

---

## 🤝 توسعه

```bash
git clone https://github.com/ahmad75naraghi/ubuntu-clipboard.git
cd ubuntu-clipboard
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# اجرا در حالت توسعه
python -m ubuntu_clipboard --hidden
python -m ubuntu_clipboard.daemon --once  # تست تک‌شات
```

**اجرای تست‌ها:**

```bash
python -c "from ubuntu_clipboard.history import HistoryManager; hm=HistoryManager(); hm.add('Hello'); print(hm.list())"
```

---

## 📄 مجوز

MIT — آزاد برای استفاده شخصی و تجاری.

---

## 🇮🇷 فارسی

این پروژه برای کاربران ایرانی اوبونتو ساخته شده تا تجربه‌ای **در حد ویندوز 11** داشته باشند — بدون نیاز به ویندوز! اگر خوشتان آمد ⭐ بدهید و با دوستان به اشتراک بگذارید.

**ساخته‌شده با ❤️ برای جامعه اوبونتو ایران**

---

## English Summary

**Ubuntu Clipboard** is a Windows 11-like clipboard manager for Ubuntu. Press **Win+V** to open a floating acrylic window with searchable history, pins, and one-click paste. Works on Wayland & X11, GNOME 42+, built with GTK4/Libadwaita and Python. Install with `./scripts/install.sh`.

