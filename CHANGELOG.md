# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[2.0.0]: https://github.com/ahmad75naraghi/ubuntu-clipboard/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/ahmad75naraghi/ubuntu-clipboard/releases/tag/v1.0.0
