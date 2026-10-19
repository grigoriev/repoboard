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
    assert ("New replies", 0) in state.sections


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
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive
        self.terminated = False

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True


def test_window_reopens_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    processes: list[FakeProcess] = []

    def popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        started.append(cmd)
        processes.append(FakeProcess())
        return processes[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    w = Window("http://127.0.0.1:1/")
    w.open()
    w.open()
    assert started[0] == [sys.executable, "-m", "repoboard.window", "http://127.0.0.1:1/"]
    assert processes[0].terminated
    w.close()
    assert processes[1].terminated
    processes[1].alive = False
    w.close()


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
