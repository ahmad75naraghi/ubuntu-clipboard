"""
settings.py — پنجره تنظیمات
"""

from __future__ import annotations

_HAS_GTK = False
try:
    import gi
    gi.require_version("Gtk","4.0")
    from gi.repository import Gtk, Gdk, GLib
    try:
        gi.require_version("Adw","1")
        from gi.repository import Adw
        _HAS_ADW = True
    except Exception:
        _HAS_ADW=False
    _HAS_GTK=True
except Exception:
    _HAS_GTK=False

from ..config import get_config
from ..history import HistoryManager

def show_settings(parent, history: HistoryManager):
    cfg = get_config()
    if not _HAS_GTK:
        # tkinter fallback
        import tkinter as tk
        from tkinter import ttk, messagebox
        win = tk.Toplevel()
        win.title("تنظیمات کلیپ‌بورد")
        win.geometry("420x380")
        win.configure(bg="#202124")
        tk.Label(win, text="تنظیمات کلیپ‌بورد", fg="white", bg="#202124", font=("Segoe UI",12,"bold")).pack(pady=12)
        # max items
        f = tk.Frame(win, bg="#202124"); f.pack(fill="x", padx=16, pady=6)
        tk.Label(f, text="حداکثر آیتم‌ها:", fg="white", bg="#202124").pack(side="left")
        var_max = tk.IntVar(value=cfg.max_items)
        tk.Spinbox(f, from_=20, to=300, textvariable=var_max, width=6).pack(side="right")
        # theme
        f2 = tk.Frame(win, bg="#202124"); f2.pack(fill="x", padx=16, pady=6)
        tk.Label(f2, text="تم:", fg="white", bg="#202124").pack(side="left")
        var_theme = tk.StringVar(value=cfg.theme)
        tk.OptionMenu(f2, var_theme, "dark","light","system").pack(side="right")
        def save():
            cfg.max_items = int(var_max.get()); cfg.theme = var_theme.get(); cfg.save()
            messagebox.showinfo("ذخیره شد","تنظیمات ذخیره شد. برای اعمال کامل برنامه را دوباره اجرا کنید.")
            win.destroy()
        tk.Button(win, text="ذخیره", command=save, bg="#8ab4f8", fg="#202124", padx=20, pady=6).pack(pady=16)
        return

    # GTK
    dlg = Gtk.Dialog(transient_for=parent, modal=True, title="تنظیمات")
    dlg.set_default_size(460, 420)
    content = dlg.get_content_area()
    content.set_spacing(12)
    content.set_margin_top(16); content.set_margin_bottom(16)
    content.set_margin_start(16); content.set_margin_end(16)

    def row(label, widget):
        b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        b.set_margin_top(6)
        b.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        b.append(widget)
        return b

    # max items
    adj = Gtk.Adjustment(value=cfg.max_items, lower=20, upper=300, step_increment=10)
    spin = Gtk.SpinButton(adjustment=adj)
    content.append(row("حداکثر آیتم‌های تاریخچه:", spin))

    # pin limit
    adj2 = Gtk.Adjustment(value=cfg.pin_limit, lower=5, upper=50, step_increment=1)
    spin2 = Gtk.SpinButton(adjustment=adj2)
    content.append(row("حداکثر سنجاق:", spin2))

    # theme
    theme_combo = Gtk.DropDown.new_from_strings(["dark","light","system"])
    theme_map = {"dark":0,"light":1,"system":2}
    theme_combo.set_selected(theme_map.get(cfg.theme,0))
    content.append(row("تم:", theme_combo))

    # width/height
    adjw = Gtk.Adjustment(value=cfg.window_width, lower=360, upper=560, step_increment=10)
    spinw = Gtk.SpinButton(adjustment=adjw)
    content.append(row("عرض پنجره:", spinw))
    adjh = Gtk.Adjustment(value=cfg.window_height, lower=420, upper=760, step_increment=10)
    spinh = Gtk.SpinButton(adjustment=adjh)
    content.append(row("ارتفاع پنجره:", spinh))

    # keep pinned on clear
    chk_keep = Gtk.CheckButton(label="هنگام پاک کردن، سنجاق‌شده‌ها بمانند")
    chk_keep.set_active(cfg.keep_pinned_on_clear)
    content.append(chk_keep)

    # exclude sensitive
    chk_sens = Gtk.CheckButton(label="نادیده گرفتن رمز/کارت بانکی (حساس)")
    chk_sens.set_active(cfg.exclude_sensitive)
    content.append(chk_sens)

    # shortcut info
    info = Gtk.Label(label="میانبر پیش‌فرض:  Super+V  (Win+V)\nبرای تغییر: Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts", xalign=0, wrap=True)
    info.set_margin_top(12)
    info.set_opacity(0.65)
    content.append(info)

    # buttons
    dlg.add_button("انصراف", Gtk.ResponseType.CANCEL)
    dlg.add_button("ذخیره", Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)

    def on_response(d, resp):
        if resp == Gtk.ResponseType.OK:
            cfg.max_items = int(spin.get_value())
            cfg.pin_limit = int(spin2.get_value())
            cfg.window_width = int(spinw.get_value())
            cfg.window_height = int(spinh.get_value())
            idx = theme_combo.get_selected()
            cfg.theme = ["dark","light","system"][idx]
            cfg.keep_pinned_on_clear = chk_keep.get_active()
            cfg.exclude_sensitive = chk_sens.get_active()
            cfg.save()
        d.close()

    dlg.connect("response", on_response)
    dlg.present()
