from __future__ import annotations

import io
import subprocess
import sys
import threading
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from repoboard import window
from repoboard.config import Config
from repoboard.gh import Gh
from repoboard.i18n import Translator
from repoboard.macos import icon_path as app_icon_path
from repoboard.models import Item, Kind, RepoSnapshot
from repoboard.poller import Poller
from repoboard.store import Store
from repoboard.tray import icon_path, tray_state
from repoboard.window import Window
from tests.fakes import FakeGhRunner

NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)


@pytest.fixture
def poller() -> Iterator[Poller]:
    store = Store(":memory:")
    yield Poller(Config(repos=("o/r",)), store, Gh(FakeGhRunner({})), clock=lambda: NOW)
    store.close()


def test_state_before_first_poll(poller: Poller) -> None:
    state = tray_state(poller, Translator())
    assert state.title == ""
    assert state.status == "Not polled yet"
    assert state.labels["quit"] == "Quit"
    assert state.labels["open_at_login"] == "Open at login"
    assert ("replies", "New replies", 0) in state.sections


def test_state_with_items(poller: Poller) -> None:
    at = NOW - timedelta(hours=2)
    issue = Item("o/r", Kind.ISSUE, "1", "T", "u", "alice", True, at, at, at, "alice")
    poller.store.save_snapshots([RepoSnapshot("o/r", (issue,))], NOW)
    poller.store.set_setting("last_poll", at.isoformat())
    poller.last_error = "rate limited"
    state = tray_state(poller, Translator("de"))
    assert state.title == "1"
    assert state.status == "Letzte Abfrage 2 Std. · rate limited"
    poller.polling = True
    assert tray_state(poller, Translator()).status.startswith("Polling")


def test_icons_exist() -> None:
    assert Path(icon_path()).is_file()
    assert Path(app_icon_path()).read_bytes().startswith(b"\x89PNG")


class FakeProcess:
    def __init__(self, exits_on_close: bool = True) -> None:
        self.alive = True
        self.terminated = False
        self.exits_on_close = exits_on_close
        self.stdin = io.BytesIO()

    def poll(self) -> int | None:
        return None if self.alive else 0

    def wait(self, timeout: float | None = None) -> int:
        sent = b"" if self.stdin.closed else self.stdin.getvalue()
        if self.alive and b"close" in sent and self.exits_on_close:
            self.alive = False
        if self.alive:
            raise subprocess.TimeoutExpired("window", timeout or 0)
        return 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False


def test_window_focuses_a_running_window_and_closes_it_gently(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    processes: list[FakeProcess] = []

    def popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        assert kwargs["stdin"] is subprocess.PIPE
        started.append(cmd)
        processes.append(FakeProcess())
        return processes[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    w = Window("http://127.0.0.1:1/")
    w.open()
    w.open()
    assert started == [[sys.executable, "-m", "repoboard.window", "http://127.0.0.1:1/"]]
    assert processes[0].stdin.getvalue() == b"focus\n"
    w.close()
    assert processes[0].stdin.getvalue() == b"focus\nclose\n"
    assert not processes[0].terminated
    w.close()
    w.open()
    assert len(started) == 2


def test_window_is_terminated_when_it_ignores_close(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(exits_on_close=False)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kwargs: process)
    w = Window("http://x/")
    w.open()
    w.close()
    assert process.terminated


def test_window_reopens_when_the_pipe_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    processes: list[FakeProcess] = []

    def popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        processes.append(FakeProcess())
        return processes[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    w = Window("http://x/")
    w.open()
    processes[0].stdin.close()
    w.open()
    assert processes[0].terminated
    assert len(processes) == 2


@pytest.mark.parametrize(
    ("commands", "calls"),
    [
        (b"focus\nfocus\nclose\nfocus\n", ["focus", "focus", "close"]),
        (b"focus\n", ["focus", "close"]),
        (b"bogus\n", ["close"]),
    ],
)
def test_follow_commands(commands: bytes, calls: list[str]) -> None:
    seen: list[str] = []
    window.follow_commands(io.BytesIO(commands), focus=lambda: seen.append("focus"), close=lambda: seen.append("close"))
    assert seen == calls


def test_window_falls_back_to_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    def popen(*args: Any, **kwargs: Any) -> None:
        raise OSError("no python")

    opened: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("webbrowser.open", opened.append)
    Window("http://x/").open()
    assert opened == ["http://x/"]
    Window("http://x/").close()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_load_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"width": 1500, "height": 900}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: FakeResponse(body))
    assert window.load_geometry("http://127.0.0.1:1/") == {"width": 1500, "height": 900}


def test_load_geometry_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert window.load_geometry("http://127.0.0.1:1/")["width"] == window.WIDTH
    window.save_geometry("http://127.0.0.1:1/", {"width": 1})


def test_save_geometry_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[urllib.request.Request] = []

    def capture(request: urllib.request.Request, timeout: float) -> FakeResponse:
        sent.append(request)
        return FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    window.save_geometry("http://127.0.0.1:1/", {"width": 1400})
    assert sent[0].full_url == "http://127.0.0.1:1/window"
    assert sent[0].get_method() == "POST"
    assert sent[0].data == b'{"width": 1400}'


def test_geometry_saver_coalesces_events() -> None:
    saved: list[dict[str, int]] = []
    done = threading.Event()

    def save(values: dict[str, int]) -> None:
        saved.append(values)
        done.set()

    saver = window.GeometrySaver(save, delay=0.05)
    saver.resized(1000, 700)
    saver.resized(1100.4, 749.6)
    assert done.wait(2)
    assert saved == [{"width": 1100, "height": 750}]
    saver.flush()
    assert len(saved) == 1


def test_geometry_saver_stores_the_content_height() -> None:
    saved: list[dict[str, int]] = []
    saver = window.GeometrySaver(saved.append, delay=10, title_bar=28)
    saver.resized(1000, 728)
    saver.flush()
    assert saved == [{"width": 1000, "height": 700}]


def test_geometry_saver_skips_fullscreen() -> None:
    saved: list[dict[str, int]] = []
    fullscreen = True
    saver = window.GeometrySaver(saved.append, delay=10, skip=lambda: fullscreen)
    saver.resized(2560, 1440)
    saver.flush()
    assert saved == []
    fullscreen = False
    saver.resized(1000, 700)
    saver.flush()
    assert saved == [{"width": 1000, "height": 700}]


def test_window_defaults_have_one_source() -> None:
    from repoboard.web import app

    assert app.WINDOW_DEFAULT == {"width": window.WIDTH, "height": window.HEIGHT}


def test_closing_from_the_menu_saves_the_size_first() -> None:
    order: list[str] = []
    saver = window.GeometrySaver(lambda values: order.append(f"save {values}"), delay=10)
    saver.resized(900, 600)
    window.close_window(saver, lambda: order.append("destroy"))()
    assert order == ["save {'width': 900, 'height': 600}", "destroy"]


def test_window_command(monkeypatch: pytest.MonkeyPatch) -> None:
    assert window.window_command("u") == [sys.executable, "-m", "repoboard.window", "u"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert window.window_command("u") == [sys.executable, "window", "u"]


def test_in_app_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    from repoboard import macos

    monkeypatch.setattr(sys, "platform", "darwin")
    assert not macos.in_app_bundle()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert macos.in_app_bundle()
    monkeypatch.setattr(sys, "platform", "linux")
    assert not macos.in_app_bundle()


def test_app_path_adds_homebrew(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from repoboard import macos

    brew = tmp_path / "brew"
    brew.mkdir()
    missing = tmp_path / "missing"
    monkeypatch.setattr(macos, "TOOL_DIRS", (str(brew), str(missing)))
    assert macos.app_path("/usr/bin:/bin") == f"{brew}:/usr/bin:/bin"
    assert macos.app_path(f"/usr/bin:{brew}") == f"/usr/bin:{brew}"
    assert macos.app_path("") == str(brew)


def test_badge_icon_draws_the_count() -> None:
    from PIL import Image

    from repoboard.tray import badge_icon

    plain = badge_icon("")
    badged = badge_icon("7")
    assert plain.size == badged.size == (64, 64)
    assert isinstance(plain, Image.Image)
    assert plain.tobytes() != badged.tobytes()
    assert badge_icon("123").tobytes() != badged.tobytes()


def test_menu_entries_follow_the_state(poller: Poller) -> None:
    from repoboard.tray import menu_entries

    entries = menu_entries(tray_state(poller, Translator("ru")))
    assert entries[0] == ("Непросмотрено", None)
    assert ("Открыть Repoboard", "open") in entries
    assert ("Обновить сейчас", "refresh") in entries
    assert entries[-1] == ("Выход", "quit")
    assert ("Новые ответы: 0", "section:replies") in entries


def test_window_falls_back_to_the_browser_when_no_gui_starts() -> None:
    opened: list[str] = []

    def no_gui() -> None:
        raise RuntimeError("You must have either QT or GTK with Python extensions installed")

    window.start_or_browse(no_gui, "http://x/", opened.append)
    assert opened == ["http://x/"]
    window.start_or_browse(lambda: None, "http://x/", opened.append)
    assert opened == ["http://x/"]


@pytest.mark.parametrize(
    ("platform", "has_gi", "hint"),
    [("linux", False, True), ("linux", True, False), ("win32", False, False)],
)
def test_linux_tray_hint(monkeypatch: pytest.MonkeyPatch, platform: str, has_gi: bool, hint: bool) -> None:
    from repoboard import tray

    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object() if has_gi else None)
    assert (tray.linux_tray_hint() is not None) is hint


def test_only_the_open_entry_is_the_default(poller: Poller) -> None:
    from repoboard.tray import default_entry, menu_entries

    entries = menu_entries(tray_state(poller, Translator()))
    defaults = [label for label, action in entries if default_entry(label, action, tray_state(poller, Translator()))]
    assert defaults == ["Open Repoboard"]


def test_menu_has_a_settings_entry(poller: Poller) -> None:
    from repoboard.tray import menu_entries

    entries = menu_entries(tray_state(poller, Translator("de")))
    assert ("Einstellungen…", "settings") in entries
    assert entries.index(("Einstellungen…", "settings")) < entries.index(("Beenden", "quit"))


def test_window_opens_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    processes: list[FakeProcess] = []

    def popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        started.append(cmd)
        processes.append(FakeProcess())
        return processes[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    w = Window("http://127.0.0.1:1/")
    w.open("/settings")
    assert started[0][-1] == "http://127.0.0.1:1/settings"
    w.open("/settings")
    assert processes[0].stdin.getvalue() == b"open http://127.0.0.1:1/settings\n"
    w.open()
    assert processes[0].stdin.getvalue().endswith(b"focus\n")


def test_follow_commands_opens_pages() -> None:
    seen: list[str] = []
    window.follow_commands(
        io.BytesIO(b"open http://x/settings\nfocus\n"),
        focus=lambda: seen.append("focus"),
        close=lambda: seen.append("close"),
        navigate=lambda url: seen.append(f"open {url}"),
    )
    assert seen == ["open http://x/settings", "focus", "focus", "close"]


def test_menu_page_per_action() -> None:
    from repoboard.tray import menu_page

    assert menu_page("open") == ""
    assert menu_page("settings") == "/settings"
    assert menu_page("section:new") == "/?section=new"
    assert menu_page("refresh") is None
    assert menu_page("quit") is None
    assert menu_page(None) is None
