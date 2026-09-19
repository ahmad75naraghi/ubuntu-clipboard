"""GNOME keybinding management (no ``sed`` in sight)."""

from __future__ import annotations

import sys

import pytest
from ubuntu_clipboard import shortcut

BINARY = "/usr/bin/ubuntu-clipboard"
OUR_PATH = shortcut.KEY_PATH
OTHER_PATH = f"{shortcut.BASE_PATH}custom0/"
LIST_KEY = f"{shortcut.SCHEMA}|{shortcut.KEY}"


def install(**kwargs):
    kwargs.setdefault("launch_command", [BINARY])
    return shortcut.install(**kwargs)


def test_quote_and_list_formatting():
    assert shortcut.quote("plain") == "'plain'"
    assert shortcut.quote("it's") == "'it\\'s'"
    assert shortcut.format_list([]) == "@as []"
    assert shortcut.format_list(["/a/", "/b/"]) == "['/a/', '/b/']"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@as []", []),
        ("[]", []),
        (None, []),
        ("['/a/']", ["/a/"]),
        ("['/a/', '/b/']", ["/a/", "/b/"]),
        ('["/a/", "/b/"]', ["/a/", "/b/"]),
        ("garbage", []),
    ],
)
def test_parse_list(raw, expected):
    assert shortcut.parse_list(raw) == expected


def test_resolve_launch_command_prefers_an_explicit_value():
    assert shortcut.resolve_launch_command("/opt/app") == ["/opt/app"]


def test_resolve_launch_command_uses_path(monkeypatch):
    monkeypatch.setattr(
        "ubuntu_clipboard.shortcut.shutil.which",
        lambda name: "/usr/bin/ubuntu-clipboard" if name == "ubuntu-clipboard" else None,
    )
    assert shortcut.resolve_launch_command() == ["/usr/bin/ubuntu-clipboard"]


def test_resolve_launch_command_falls_back_to_the_interpreter():
    command = shortcut.resolve_launch_command(which=lambda _name: None)
    assert command == [sys.executable, "-m", "ubuntu_clipboard"]


def test_resolve_launch_command_prefers_local_bin(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    local = tmp_path / ".local" / "bin" / "ubuntu-clipboard"
    local.parent.mkdir(parents=True)
    local.write_text("#!/bin/sh\n", encoding="utf-8")
    assert shortcut.resolve_launch_command(which=lambda _name: None) == [str(local)]


def test_install_registers_the_binding(fake_gsettings):
    fake = fake_gsettings()
    report = install(runner=fake)
    assert report.ok is True
    assert fake.values[LIST_KEY] == f"['{OUR_PATH}']"
    assert fake.values[f"{shortcut.CHILD_SCHEMA}:{OUR_PATH}|binding"] == "'<Super>v'"
    assert fake.values[f"{shortcut.CHILD_SCHEMA}:{OUR_PATH}|command"] == f"'{BINARY} --toggle'"
    assert "Win+V" in fake.values[f"{shortcut.CHILD_SCHEMA}:{OUR_PATH}|name"]


def test_install_is_idempotent(fake_gsettings):
    fake = fake_gsettings()
    install(runner=fake)
    first = dict(fake.values)
    install(runner=fake)
    assert fake.values == first


def test_install_keeps_foreign_bindings(fake_gsettings):
    fake = fake_gsettings(
        {
            LIST_KEY: f"['{OTHER_PATH}']",
            f"{shortcut.SCHEMA}:{OTHER_PATH}|command": "'/usr/bin/other-tool'",
        }
    )
    install(runner=fake)
    assert fake.values[LIST_KEY] == f"['{OTHER_PATH}', '{OUR_PATH}']"


def test_install_removes_stale_duplicates(fake_gsettings):
    fake = fake_gsettings(
        {
            LIST_KEY: f"['{OTHER_PATH}', '{OUR_PATH}']",
            f"{shortcut.CHILD_SCHEMA}:{OTHER_PATH}|command": f"'{BINARY} --toggle'",
        }
    )
    report = install(runner=fake)
    assert fake.values[LIST_KEY] == f"['{OUR_PATH}']"
    assert OTHER_PATH in report.removed


def test_install_unbinds_conflicting_shell_shortcut(fake_gsettings):
    fake = fake_gsettings({f"{shortcut.SHELL_SCHEMA}|{shortcut.SHELL_TOGGLE_KEY}": "['<Super>v']"})
    report = install(runner=fake)
    assert fake.values[f"{shortcut.SHELL_SCHEMA}|{shortcut.SHELL_TOGGLE_KEY}"] == "@as []"
    assert report.disabled
    assert any("conflicting" in message for message in report.messages)


def test_install_can_keep_the_conflict(fake_gsettings):
    fake = fake_gsettings({f"{shortcut.SHELL_SCHEMA}|{shortcut.SHELL_TOGGLE_KEY}": "['<Super>v']"})
    report = install(runner=fake, unbind_conflicts=False)
    assert fake.values[f"{shortcut.SHELL_SCHEMA}|{shortcut.SHELL_TOGGLE_KEY}"] == "['<Super>v']"
    assert report.disabled == []
    assert any("may win" in message for message in report.messages)


def test_install_reports_rejected_values(fake_gsettings):
    class Refusing(type(fake_gsettings())):
        def __call__(self, command, **kwargs):
            if command[1] == "set" and command[3] == "binding":
                import subprocess

                return subprocess.CompletedProcess(list(command), 1, b"", b"invalid")
            return super().__call__(command, **kwargs)

    fake = Refusing({})
    report = install(runner=fake)
    assert report.ok is False
    assert any("rejected" in message for message in report.messages)


def test_install_requires_gsettings(monkeypatch):
    monkeypatch.setattr("ubuntu_clipboard.shortcut.shutil.which", lambda _name: None)
    with pytest.raises(shortcut.ShortcutError):
        install()


def test_custom_binding_is_used(fake_gsettings):
    fake = fake_gsettings()
    report = install(runner=fake, binding="<Control><Alt>v")
    assert report.binding == "<Control><Alt>v"
    assert fake.values[f"{shortcut.CHILD_SCHEMA}:{OUR_PATH}|binding"] == "'<Control><Alt>v'"


def test_status_reports_the_current_state(fake_gsettings):
    fake = fake_gsettings()
    install(runner=fake)
    state = shortcut.status(runner=fake)
    assert state["ours_listed"] is True
    assert state["binding"] == "'<Super>v'"
    assert OUR_PATH in state["registered_paths"]


def test_status_without_any_entry(fake_gsettings):
    fake = fake_gsettings()
    state = shortcut.status(runner=fake)
    assert state["ours_listed"] is False
    assert state["binding"] is None


def test_uninstall_removes_our_binding(fake_gsettings):
    fake = fake_gsettings()
    install(runner=fake)
    report = shortcut.uninstall(runner=fake)
    assert report.ok is True
    assert fake.values[LIST_KEY] == "@as []"
    assert OUR_PATH in report.removed


def test_uninstall_keeps_foreign_bindings(fake_gsettings):
    fake = fake_gsettings(
        {
            LIST_KEY: f"['{OTHER_PATH}']",
            f"{shortcut.SCHEMA}:{OTHER_PATH}|command": "'/usr/bin/other-tool'",
        }
    )
    install(runner=fake)
    assert fake.values[LIST_KEY] == f"['{OTHER_PATH}', '{OUR_PATH}']"
    shortcut.uninstall(runner=fake)
    assert fake.values[LIST_KEY] == f"['{OTHER_PATH}']"


def test_uninstall_restores_the_shell_shortcut_only_when_empty(fake_gsettings):
    shell_key = f"{shortcut.SHELL_SCHEMA}|{shortcut.SHELL_TOGGLE_KEY}"
    fake = fake_gsettings({shell_key: "['<Super>v']"})
    install(runner=fake)
    shortcut.uninstall(runner=fake)
    assert fake.values[shell_key] == "['<Super>v']"

    fake = fake_gsettings({shell_key: "['<Super>m']"})
    install(runner=fake)
    shortcut.uninstall(runner=fake)
    assert fake.values[shell_key] == "['<Super>m']"


def test_uninstall_requires_gsettings(monkeypatch):
    monkeypatch.setattr("ubuntu_clipboard.shortcut.shutil.which", lambda _name: None)
    with pytest.raises(shortcut.ShortcutError):
        shortcut.uninstall()


def test_owns_binding_detects_both_commands(fake_gsettings):
    fake = fake_gsettings({f"{shortcut.CHILD_SCHEMA}:{OTHER_PATH}|command": "'python3 -m ubuntu_clipboard'"})
    assert shortcut.owns_binding(OTHER_PATH, runner=fake) is True
    fake.values[f"{shortcut.CHILD_SCHEMA}:{OTHER_PATH}|command"] = "'/usr/bin/something-else'"
    assert shortcut.owns_binding(OTHER_PATH, runner=fake) is False


def test_report_records_messages():
    report = shortcut.Report()
    report.add("hello")
    assert report.messages == ["hello"]


def test_install_shows_what_gsettings_said(fake_gsettings):
    """The 2.0.0 installer only said "did not complete" — never why."""

    class Refusing(type(fake_gsettings())):
        def __call__(self, command, **kwargs):
            if command[1] == "set" and command[3] == "binding":
                import subprocess

                return subprocess.CompletedProcess(list(command), 1, b"", b"No such schema")
            return super().__call__(command, **kwargs)

    report = install(runner=Refusing({}))
    assert report.ok is False
    assert any("No such schema" in message for message in report.messages)


def test_set_value_checked_explains_why_it_failed():
    import subprocess

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("gsettings")

    def slow(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("gsettings", 10)

    def refusing(command, **_kwargs):
        return subprocess.CompletedProcess(list(command), 1, b"", b"unknown keyword")

    assert "not installed" in shortcut.set_value_checked("s", "k", "v", runner=missing)
    assert "did not answer" in shortcut.set_value_checked("s", "k", "v", runner=slow)
    assert "unknown keyword" in shortcut.set_value_checked("s", "k", "v", runner=refusing)
    assert shortcut.set_value_checked("s", "k", "v", runner=lambda *_a, **_k: None) == (
        "gsettings could not be run"
    )


def test_bindings_are_written_to_the_relocatable_child_schema(fake_gsettings):
    """``...media-keys:<path>`` is rejected by gsettings (not relocatable).

    2.0.0 sent ``name``/``command``/``binding`` to the parent schema and every
    install failed with "path must not be specified".
    """
    fake = fake_gsettings()
    report = install(runner=fake)
    assert report.ok is True, report.messages

    targets = {command[2] for command in fake.calls if command[1] in {"get", "set"}}
    parent_with_path = [target for target in targets if target.startswith(f"{shortcut.SCHEMA}:")]
    assert parent_with_path == [], f"wrote to a non-relocatable schema: {parent_with_path}"
    assert f"{shortcut.CHILD_SCHEMA}:{shortcut.KEY_PATH}" in targets
    assert shortcut.SCHEMA in targets  # the binding list itself has no path


def test_status_reads_the_child_schema(fake_gsettings):
    fake = fake_gsettings(
        {
            f"{shortcut.SCHEMA}|{shortcut.KEY}": f"['{shortcut.KEY_PATH}']",
            f"{shortcut.CHILD_SCHEMA}:{shortcut.KEY_PATH}|name": "'Clipboard — Win+V'",
            f"{shortcut.CHILD_SCHEMA}:{shortcut.KEY_PATH}|command": "'/usr/bin/ubuntu-clipboard --toggle'",
            f"{shortcut.CHILD_SCHEMA}:{shortcut.KEY_PATH}|binding": "'<Super>v'",
        }
    )
    state = shortcut.status(runner=fake)
    assert state["ours_listed"] is True
    assert state["binding"] == "'<Super>v'"
    assert "ubuntu-clipboard" in state["command"]


def test_fake_gsettings_matches_the_real_schema_rules(fake_gsettings):
    """The double has to reject what gsettings rejects, or bugs stay hidden."""
    fake = fake_gsettings()
    wrong = shortcut.get_value(f"{shortcut.SCHEMA}:{shortcut.KEY_PATH}", "name", runner=fake)
    assert wrong is None
    right = shortcut.set_value(f"{shortcut.CHILD_SCHEMA}", "name", "'x'", shortcut.KEY_PATH, runner=fake)
    assert right is True
    missing_path = shortcut.set_value(shortcut.CHILD_SCHEMA, "name", "'x'", runner=fake)
    assert missing_path is False


def test_unquote_reads_gvariant_strings():
    assert shortcut.unquote("'<Super>v'") == "<Super>v"
    assert shortcut.unquote('"<Super>v"') == "<Super>v"
    assert shortcut.unquote("@as []") == "[]"
    assert shortcut.unquote(None) == ""
    assert shortcut.unquote("") == ""


def test_custom_conflicts_finds_the_users_own_shortcut(fake_gsettings):
    """A second shortcut on <Super>v is why Win+V can look dead — never edit it."""
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
    fake = fake_gsettings(
        {
            f"{shortcut.SCHEMA}|{shortcut.KEY}": f"['{other}', '{shortcut.KEY_PATH}']",
            f"{shortcut.CHILD_SCHEMA}:{other}|binding": "'<Super>v'",
            f"{shortcut.CHILD_SCHEMA}:{other}|command": "'/usr/bin/keepassxc --toggle'",
        }
    )
    clashes = shortcut.custom_conflicts(runner=fake)
    assert len(clashes) == 1 and "custom0" in clashes[0] and "keepassxc" in clashes[0]
    # our own entry and other keys are not reported
    assert all(shortcut.KEY_PATH not in clash for clash in clashes)


def test_custom_conflicts_ignores_a_different_binding(fake_gsettings):
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/"
    fake = fake_gsettings(
        {
            f"{shortcut.SCHEMA}|{shortcut.KEY}": f"['{other}']",
            f"{shortcut.CHILD_SCHEMA}:{other}|binding": "'<Super>m'",
        }
    )
    assert shortcut.custom_conflicts(runner=fake) == []


def test_install_warns_about_the_users_shortcut_but_succeeds(fake_gsettings):
    other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/"
    fake = fake_gsettings(
        {
            f"{shortcut.SCHEMA}|{shortcut.KEY}": f"['{other}']",
            f"{shortcut.CHILD_SCHEMA}:{other}|binding": "'<Super>v'",
            f"{shortcut.CHILD_SCHEMA}:{other}|command": "'/usr/bin/keepassxc --toggle'",
        }
    )
    report = install(runner=fake)
    assert report.ok is True
    assert report.clashing and "custom0" in report.clashing[0]
    assert any(
        message.startswith("warning:") and "already uses <Super>v" in message for message in report.messages
    )
    # the user's entry must be untouched, and still registered
    assert fake.values[f"{shortcut.CHILD_SCHEMA}:{other}|binding"] == "'<Super>v'"
    assert other in fake.values[f"{shortcut.SCHEMA}|{shortcut.KEY}"]
