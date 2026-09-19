# راهنمای مشارکت / Contributing

ممنون که می‌خواهید کمک کنید! این پروژه یک کلیپ‌بورد GTK 4 برای اوبونتو است و
کمترین انتظار ما از هر تغییر، «کار کردن بدون نمایشگر در تست‌ها» است.

## راه‌اندازی محیط توسعه

```bash
git clone https://github.com/ahmad75naraghi/ubuntu-clipboard.git
cd ubuntu-clipboard
python3 -m venv .venv
.venv/bin/pip install pytest ruff
.venv/bin/pip install --no-deps PyGObject-stubs   # برای بررسی استاتیک GTK
```

GTK/PyGObject از توزیع نصب می‌شود و در PyPI قابل نصب نیست:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
```

## اجرای بررسی‌ها

```bash
make check       # ruff check + ruff format --check + pytest
make test
make lint
make format      # اصلاح خودکار قالب‌بندی
make gtk-check   # بررسی استاتیک APIهای GTK/GDK/Adw (نیاز به PyGObject-stubs)
```

پوشش تست‌ها دو بخش دارد:

* `tests/test_*.py` — ماژول‌های بدون GTK (config، storage، models، shortcut، install، cli و ...).
* `tests/test_ui.py` و `tests/test_app.py` — منطق رابط کاربری، تنظیمات، مانیتور کلیپ‌بورد
  و منطق برنامه روی `tests/gtk_double.py`، یک جایگزین سبک GTK. اگر کلاس یا متدی از
  GTK لازم شد، آن را به `gtk_double.py` اضافه کنید (به‌جای mock سراسری).
* `tests/test_entrypoints.py` — برنامه را در یک **مفسر تازه** (`python -m ubuntu_clipboard`)
  با همان `gi` جعلی روی `PYTHONPATH` اجرا می‌کند؛ این‌جا مسیر واقعی entry point، چرخهٔ
  `Gtk.Application` و فایل‌های نوشته‌شده زیر `$XDG_*` آزموده می‌شوند.

پیش از هر Pull Request باید این چهار مورد بدون خطا اجرا شوند. CI همین‌ها را روی
Python 3.10، 3.12 و 3.13 اجرا می‌کند و در یک job جدا هم sdist/wheel را می‌سازد،
داده‌های بسته (`ui/styles.css` و آیکون hicolor) را بررسی و نسخهٔ نصب‌شده را اجرا می‌کند.

## قواعد کد

- **پایتون ۳.۱۰+**، `from __future__ import annotations`، تایپ‌های PEP 604
  (`str | None`).
- حداکثر طول خط ۱۱۰ کاراکتر (ruff).
- فقط کتابخانه استاندارد؛ هیچ وابستگی زمان اجرا از PyPI اضافه نکنید.
- کدی که به GTK نیاز دارد باید **در زمان import** به GTK وابسته نباشد تا CLI و
  تست‌ها headless کار کنند. الگو:

  ```python
  try:  # pragma: no cover - depends on the environment
      import gi

      gi.require_version("Gtk", "4.0")
      from gi.repository import Gtk

      HAS_GTK = True
  except (ImportError, ValueError):
      Gtk = None
      HAS_GTK = False
  ```

- هیچ `except Exception: pass` بی‌توضیحی نگذارید؛ یا لاگ کنید یا از
  `contextlib.suppress` با کامنت دلیل استفاده کنید.
- برای هر باگ یک تست بنویسید که بدون آن تست شکست بخورد.
- رشته‌های کاربری در `ubuntu_clipboard/i18n.py` و در هر دو زبان فارسی و انگلیسی
  اضافه شوند (تست `tests/test_i18n.py` این را الزامی می‌کند).
- کامیت‌ها و کد به انگلیسی، متن رابط کاربری و مستندات کاربر به فارسی.

## تست‌ها

- هر ماژول یک فایل `tests/test_<module>.py` دارد.
- از fixtureهای `tests/conftest.py` استفاده کنید: `isolated_home` (متغیرهای
  `XDG_*` موقت)، `config`، `store` و `fake_gsettings`.
- هیچ تستی نباید به شبکه، نمایشگر، دیتابیس واقعی کاربر یا سرویس D-Bus وابسته باشد.
- برای GTK از mock/injection استفاده کنید (به‌عنوان مثال `runner=` و `which=` در
  `shortcut.py`, `clipboard.py` و `paste.py`).

## گزارش باگ

خروجی `ubuntu-clipboard --collect-logs` را به Issue پیوست کنید؛ این فایل
نسخه، محیط نشست، وضعیت دیتابیس، ابزارهای موجود و ۳۰۰ خط آخر لاگ را دارد.
