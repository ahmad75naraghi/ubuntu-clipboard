# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.6] — 2026-09-19

### Fixed

- The keybinding is now written **before** its path is added to
  `custom-keybindings`. `gnome-settings-daemon` reacts to the list change and
  reads `name`/`command`/`binding` at that moment; writing the path first made
  it see an empty binding, so `--status` reported a registered shortcut that
  could never fire.
- After restarting the plugin, the tool now waits for it to come back and asks
  D-Bus to activate it if it does not; when it stays down the user is warned
  ("log out and back in") instead of being told everything is reloaded.
  Ubuntu 24.04 refuses `systemctl --user restart
  org.gnome.SettingsDaemon.MediaKeys` ("may be requested by dependency only"),
  which used to be the end of the story.

## [2.0.5] — 2026-09-19

### Added

- `--diagnose`: a checklist that answers "why does Win+V do nothing?" — is GTK
  installed, is the service running, is the binding registered, does the
  command still point at an existing program, does another shortcut or the
  GNOME shell own the key, is `gsd-media-keys` alive — followed by the
  problems found and the fastest fixes.
- `--binding KEYS` (with `--install`/`--install-shortcut`): register a
  different key and remember it in the configuration.
- After a successful install the shortcut daemon is asked to reload
  (`systemctl --user restart org.gnome.SettingsDaemon.MediaKeys`, falling back
  to `pkill -f gsd-media-keys`), because a freshly written keybinding is
  sometimes only picked up on the next login. The result is reported.

## [2.0.4] — 2026-09-19

### Added

- `--take-binding` (with `--install` or `--install-shortcut`): removes the
  *other* custom shortcuts that already use our key. Without it those entries
  stay untouched (they belong to the user) and are only reported.
- The conflict warning now names known clipboard managers (`diodon`, `copyq`,
  `clipit`, `parcellite`, `gpaste`, `clipman`, `cliphist`, `greenclip`) so the
  usual reason Win+V "does nothing" is obvious: another clipboard manager holds
  the key. `shortcut.foreign_bindings()`, `describe_foreign()` and
  `remove_paths()` are the building blocks; `Report.took` lists what was
  removed.

## [2.0.3] — 2026-09-19

### Added

- `--install-shortcut` (and `--install`) now warns when *another* custom
  shortcut already uses the same key — the usual reason Win+V "does nothing"
  even though our binding is registered. The user's entry is never modified;
  the warning names the path and the command so it can be removed in
  Settings → Keyboard → Custom Shortcuts. `Report.clashing` exposes the list
  and the CLI prints those lines as warnings rather than successes.

## [2.0.2] — 2026-09-19

The Win+V keybinding could never be installed: gsettings answered

    Schema "org.gnome.settings-daemon.plugins.media-keys" is not relocatable
    (path must not be specified)

### Fixed

- `name`, `command` and `binding` are now written to the *relocatable child*
  schema `org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:<path>`
  instead of appending the path to the parent schema, which gsettings rejects.
  Reading them back (`--status`, `owns_binding`) uses the same schema, so
  `--status` shows `'<Super>v' -> '… --toggle'` instead of `- -> -`.
- `tests/conftest.py`'s gsettings double now enforces the real schema rules
  (unknown schema, missing path on a relocatable schema, path on a
  non-relocatable one). Accepting every target is what let 2.0.0 ship with a
  keybinding install that could not work.

## [2.0.1] — 2026-09-19

Fixes two crashes that only show up with a real PyGObject (the test doubles
accepted both calls), found while installing 2.0.0 on Ubuntu 24.04.

### Fixed

- **`--background`, `--toggle` and friends died on startup** with
  ``AttributeError: 'ClipboardApplication' object has no attribute
  'set_application_name'``. `Gio.Application` has no such method; the GLib
  global `GLib.set_application_name()` is the real API.
- **`Gdk` was imported without a version pin**, so PyGObject printed
  ``PyGIWarning`` and was free to pick GTK 3. `gi.require_version("Gdk", "4.0")`
  is now set in `app.py` and `ui/__init__.py`.

### Changed

- `scripts/check_gtk_api.py` grew the two rules that would have caught the
  above: every `gi.repository` import needs a `gi.require_version`, and every
  `self.attribute` inside a class derived from Gtk/Gdk/Adw must exist on that
  class. `tests/test_gtk_checker.py` keeps the rules honest.
- A failed shortcut install now prints the reason from `gsettings` (including
  its stderr) instead of "did not complete"; `--status` says
  "not registered — ubuntu-clipboard --install-shortcut" instead of `- -> -`.
- An unexpected error is written to the application log and reported as one
  line (plus the log path); `--debug` still prints the full traceback.
- `scripts/install.sh` shows the output of the background start when the
  service does not come up, instead of only warning that it is not running.

## [2.0.0] — 2026-09-19

A full rewrite. The user interface, the storage layer and the installer were replaced;
version 2.0.0 fixes the correctness problems that made 1.x unreliable.

### Added

- Command line interface (`ubuntu-clipboard --help`) with window, history, integration
  and diagnostics groups; every non-GUI command works headlessly and over SSH.
- `--status`, `--logs`, `--clear-logs` and `--collect-logs` for support requests.
- Automatic migration of the 1.x database (`~/.config/ubuntu-clipboard/history.db` →
  `~/.local/share/ubuntu-clipboard/history.db`) including base64 images → BLOBs.
- Privacy filters for credit card numbers, `password=`/`api_key=` style lines and PEM
  private keys, plus support for the `x-kde-passwordManagerHint` clipboard marker.
- English and Persian translations with automatic RTL layout, `language: "auto"`.
- Test suite of 276 headless tests, including `tests/gtk_double.py`, a small stand-in for
  GTK that exercises the window, the preferences dialog, the clipboard monitor and the
  application object without a display, and `tests/test_entrypoints.py`, which starts the
  real `python -m ubuntu_clipboard` entry point in a fresh interpreter.
- `scripts/check_gtk_api.py`, a static verifier for the GTK 4 / libadwaita API surface,
  which catches calls such as `Gdk.ToplevelState.ACTIVE` before they reach a user.
- GitHub Actions workflow running ruff, the GTK API check and pytest.

### Changed

- **Single instance without lock files.** `Gio.Application` with
  `HANDLES_COMMAND_LINE` replaces the `*.lock` + `SIGKILL` mechanism. `Win+V` now either
  becomes the primary instance or hands the command to the running one over D-Bus.
- **The process stays resident**, so it keeps ownership of the selection it writes.
  In 1.x the window exited 350 ms after copying, which made pasted content disappear.
- **Event driven clipboard monitoring** through `Gdk.Clipboard` signals instead of
  polling `wl-paste` four times per second.
- **GTK 4 + libadwaita** user interface, replacing the GTK 3 window and the Tkinter
  fallback. The stylesheet only uses properties GTK 4 supports.
- **Python based desktop integration** (`ubuntu_clipboard/install.py`) replaces the
  `sed`/`cp` scripts, and GNOME keybindings are parsed and rewritten as data
  (`ubuntu_clipboard/shortcut.py`) instead of being patched textually.
- SQLite storage rewritten: WAL journal, thread-local connections with a five second
  busy timeout, per item size accounting, typed columns and a real `user_version`
  schema migration.
- `scripts/install.sh` installs distribution packages, creates a
  `--system-site-packages` virtualenv (PEP 668 safe) and verifies the service start;
  `scripts/uninstall.sh` gained `--purge` and a manual fallback.
- Documentation rewritten to match the implementation, with a real preview image.

### Removed

- `ubuntu_clipboard/daemon.py`, `history.py`, `indicator.py` and `tray.py`
  (the AppIndicator tray was non functional on GNOME and is gone).
- `requirements.txt` (the package has no third party runtime dependencies) and the
  generated desktop files under `data/`, which are now written by `--install`.
- `scripts/setup-shortcut.sh` and `scripts/collect-logs.sh`, superseded by
  `--install-shortcut` and `--collect-logs`.

### Fixed

- Clipboard content no longer vanishes when the popup closes.
- Repeated identical copies update the existing entry (counter + recency) instead of
  creating duplicates, and never move pinned items.
- LIKE wildcards in the search box are escaped, so searching for `%` or `_` is exact.
- Bounded, rotating log file instead of three unbounded ones.
- Desktop entry, autostart file and icon are always consistent with the installed
  interpreter, so `TryExec`/`Exec` no longer point at a stale path.

## [1.0.0] — 2026-05-24

Initial release: floating Win+V window with search, pins and one click paste.

[2.0.6]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v2.0.5...v2.0.6
[2.0.5]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v2.0.4...v2.0.5
[2.0.4]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v2.0.3...v2.0.4
[2.0.3]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v2.0.2...v2.0.3
[2.0.2]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/ahmad75naraghi/ubuntu-clipboard/releases/tag/v1.0.0
