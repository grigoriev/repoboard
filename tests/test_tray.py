from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

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
