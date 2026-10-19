from __future__ import annotations

import subprocess
import sys
import types
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from repoboard import notify
from repoboard.i18n import Translator
from repoboard.models import Item, Kind
from repoboard.notify import (
    DesktopNotifier,
    LogNotifier,
    Notification,
    NotificationListener,
    OsascriptNotifier,
    build,
)
from repoboard.store import Entry
from repoboard.triage import Section

NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)
T = Translator()


def entry(section: Section, kind: Kind = Kind.ISSUE, number: str = "1") -> Entry:
    item = Item(
        repo="o/repo",
        kind=kind,
        number=number,
        title=f"Title {number}",
        url=f"https://github.com/o/repo/{number}",
        author="alice",
        open=True,
        created_at=NOW,
        updated_at=NOW,
        activity_at=NOW,
        activity_by="bob",
    )
    return Entry(item=item, section=section, marker=NOW, unseen=True)


@pytest.mark.parametrize(
    ("section", "kind", "title"),
    [
        (Section.NEW, Kind.ISSUE, "New issue in repo"),
        (Section.NEW, Kind.DISCUSSION, "New discussion in repo"),
        (Section.NEW, Kind.PR, "New pull request in repo"),
        (Section.REPLIES, Kind.ISSUE, "bob replied in repo"),
        (Section.SECURITY, Kind.ALERT, "Security alert in repo"),
        (Section.FAILED, Kind.PR, "Pull request failed in repo"),
        (Section.RENOVATE, Kind.PR, "Renovate waits in repo"),
    ],
)
def test_single(section: Section, kind: Kind, title: str) -> None:
    (n,) = build([entry(section, kind)], T, "http://inbox")
    assert n == Notification(title, "Title 1", "https://github.com/o/repo/1")


def test_reply_without_login() -> None:
    e = entry(Section.REPLIES)
    e = replace(e, item=replace(e.item, activity_by=None))
    assert build([e], T, "u")[0].title == " replied in repo"


def test_summary_above_limit() -> None:
    entries = [entry(Section.NEW, number=str(n)) for n in range(4)]
    assert build(entries, Translator("ru"), "http://inbox") == [
        Notification("Repoboard: новых 4", "Открой Repoboard, чтобы посмотреть.", "http://inbox")
    ]


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Notification] = []
        self.fail = fail

    def send(self, notification: Notification) -> None:
        if self.fail:
            raise RuntimeError("no backend")
        self.sent.append(notification)


def test_listener_sends_and_survives_failures(caplog: pytest.LogCaptureFixture) -> None:
    recorder = Recorder()
    NotificationListener(recorder, lambda: T, "u")([entry(Section.NEW)])
    assert len(recorder.sent) == 1
    NotificationListener(Recorder(fail=True), lambda: T, "u")([entry(Section.NEW)])
    assert "notification failed" in caplog.text


def test_desktop_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class FakeSync:
        def __init__(self, app_name: str) -> None:
            sent["app"] = app_name

        def request_authorisation(self) -> bool:
            return True

        def send(self, **kwargs: Any) -> str:
            sent.update(kwargs)
            return "id"

    install_fake_desktop_notifier(monkeypatch, FakeSync, backend="MacOSNotificationCenter")
    opened: list[str] = []
    DesktopNotifier(open_url=opened.append).send(Notification("T", "B", "https://x"))
    assert (sent["app"], sent["title"], sent["message"]) == ("Repoboard", "T", "B")
    sent["on_clicked"]()
    assert opened == ["https://x"]


def install_fake_desktop_notifier(monkeypatch: pytest.MonkeyPatch, sync: type, backend: str) -> None:
    module = types.ModuleType("desktop_notifier")
    module.DesktopNotifierSync = sync  # type: ignore[attr-defined]
    main = types.ModuleType("desktop_notifier.main")
    main.get_backend_class = lambda: type(backend, (), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "desktop_notifier", module)
    monkeypatch.setitem(sys.modules, "desktop_notifier.main", main)


def test_desktop_notifier_refuses_dummy_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_desktop_notifier(monkeypatch, object, backend="DummyNotificationCenter")
    with pytest.raises(RuntimeError, match="app bundle"):
        DesktopNotifier()


def test_osascript_passes_text_as_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    OsascriptNotifier().send(Notification('T "quoted"', "B", "u"))
    assert calls[0][0] == "osascript"
    assert calls[0][-2:] == ['T "quoted"', "B"]


def test_log_notifier(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger=notify.__name__)
    LogNotifier().send(Notification("T", "B", "u"))
    assert "T: B u" in caplog.text


@pytest.mark.parametrize(
    ("kind", "title"),
    [
        (Kind.ISSUE, "Neues Issue in repo"),
        (Kind.DISCUSSION, "Neue Discussion in repo"),
        (Kind.PR, "Neuer Pull Request in repo"),
    ],
)
def test_german_titles_follow_the_gender(kind: Kind, title: str) -> None:
    assert build([entry(Section.NEW, kind)], Translator("de"), "u")[0].title == title


def test_desktop_notifier_refuses_without_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    class Denied:
        def __init__(self, app_name: str) -> None:
            pass

        def request_authorisation(self) -> bool:
            return False

    install_fake_desktop_notifier(monkeypatch, Denied, backend="MacOSNotificationCenter")
    with pytest.raises(RuntimeError, match="not allowed"):
        DesktopNotifier()


def test_terminal_notifier_opens_the_item(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    notify.TerminalNotifier("/opt/homebrew/bin/terminal-notifier").send(Notification("T", "B", "https://x"))
    assert calls == [
        [
            "/opt/homebrew/bin/terminal-notifier",
            "-title",
            "T",
            "-message",
            "B",
            "-open",
            "https://x",
            "-group",
            "https://x",
        ]
    ]


@pytest.mark.parametrize(
    ("terminal", "platform", "expected"),
    [
        ("/bin/terminal-notifier", "darwin", notify.TerminalNotifier),
        (None, "darwin", OsascriptNotifier),
        (None, "linux", LogNotifier),
    ],
)
def test_fallback_notifier_order(
    monkeypatch: pytest.MonkeyPatch, terminal: str | None, platform: str, expected: type
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: terminal)
    monkeypatch.setattr("sys.platform", platform)
    assert isinstance(notify.fallback_notifier(), expected)


def test_deferred_notifier_keeps_the_fallback_when_native_fails(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger=notify.__name__)
    fallback = Recorder()

    def broken() -> Recorder:
        raise RuntimeError("not allowed")

    deferred = notify.DeferredNotifier(broken, fallback)
    assert deferred.wait_ready(5)
    deferred.send(Notification("t", "b", "u"))
    assert len(fallback.sent) == 1
    assert "not allowed" in caplog.text


@pytest.mark.parametrize(
    ("text", "safe"),
    [
        ("[Bug] crash", "\\[Bug] crash"),
        ("(draft) x", "\\(draft) x"),
        ('"quoted"', '\\"quoted"'),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_plist_safe(text: str, safe: str) -> None:
    assert notify.plist_safe(text) == safe


def test_terminal_notifier_escapes_a_leading_bracket(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    notify.TerminalNotifier("tn").send(Notification("[Bug] in repo", "{x}", "u"))
    assert calls[0][1:5] == ["-title", "\\[Bug] in repo", "-message", "\\{x}"]


def test_deferred_notifier_does_not_block_and_takes_over_when_ready() -> None:
    import threading

    ready = threading.Event()
    native = Recorder()
    fallback = Recorder()

    def build() -> Recorder:
        ready.wait(5)
        return native

    deferred = notify.DeferredNotifier(build, fallback)
    deferred.send(Notification("early", "b", "u"))
    ready.set()
    assert deferred.wait_ready(5)
    deferred.send(Notification("late", "b", "u"))
    assert [n.title for n in fallback.sent] == ["early"]
    assert [n.title for n in native.sent] == ["late"]
