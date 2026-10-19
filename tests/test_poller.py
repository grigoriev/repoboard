from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from repoboard.config import Config
from repoboard.gh import Gh, GhError
from repoboard.poller import Poller, utc_now
from repoboard.store import Entry, Store
from repoboard.triage import Section
from tests.fakes import FakeGhRunner, empty_repo_routes

NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def store() -> Iterator[Store]:
    s = Store(":memory:")
    yield s
    s.close()


def issues(created: str) -> list[dict[str, Any]]:
    return [
        {
            "number": 1,
            "title": "Bug",
            "url": "https://github.com/o/r/issues/1",
            "author": {"login": "alice"},
            "state": "OPEN",
            "createdAt": created,
            "updatedAt": created,
        }
    ]


def make_poller(store: Store, routes: dict[str, Any], clock: Clock) -> tuple[Poller, FakeGhRunner]:
    runner = FakeGhRunner({"api user": {"login": "me"}, **routes})
    return Poller(Config(repos=("o/r",)), store, Gh(runner), clock), runner


def test_utc_now_is_aware() -> None:
    assert utc_now().tzinfo is not None


def test_first_poll_is_silent_then_notifies(store: Store) -> None:
    clock = Clock()
    routes = {**empty_repo_routes(), "issue list": issues("2026-10-19T08:00:00Z")}
    poller, runner = make_poller(store, routes, clock)
    heard: list[list[Entry]] = []
    poller.listeners.append(heard.append)
    assert poller.poll() == []
    assert len(store.inbox(NOW)[Section.NEW]) == 1
    assert poller.last_poll() == NOW
    routes["issue list"] = issues("2026-10-19T09:30:00Z")
    runner.routes.update(routes)
    clock.now = NOW + timedelta(hours=1)
    fresh = poller.poll()
    assert [e.item.number for e in fresh] == ["1"]
    assert heard == [[], fresh]
    assert sum(1 for call in runner.calls if call == ["api", "user"]) == 1


def test_poll_error_is_recorded(store: Store) -> None:
    poller, _ = make_poller(store, {"api user": GhError("auth required", 401)}, Clock())
    assert poller.poll() == []
    assert poller.last_error == "auth required"
    assert poller.polling is False
    assert poller.last_poll() is None


def test_concurrent_poll_is_skipped(store: Store) -> None:
    poller, _ = make_poller(store, empty_repo_routes(), Clock())
    poller._lock.acquire()
    try:
        assert poller.poll() == []
    finally:
        poller._lock.release()


def test_background_thread_polls_and_refreshes(store: Store) -> None:
    polled = threading.Event()
    poller, _ = make_poller(store, empty_repo_routes(), Clock())
    count = 0

    def listener(entries: list[Entry]) -> None:
        nonlocal count
        count += 1
        if count == 2:
            polled.set()

    poller.listeners.append(listener)
    poller.start()
    poller.refresh()
    assert polled.wait(5)
    poller.stop()
    assert poller._thread is not None
    poller._thread.join(5)
    assert not poller._thread.is_alive()


def test_first_successful_poll_is_silent_after_a_failed_one(store: Store) -> None:
    clock = Clock()
    routes = {**empty_repo_routes(), "api user": GhError("offline"), "issue list": issues("2026-10-19T08:00:00Z")}
    poller, runner = make_poller(store, routes, clock)
    assert poller.poll() == []
    runner.routes["api user"] = {"login": "me"}
    assert poller.poll() == []
    assert len(store.inbox(NOW)[Section.NEW]) == 1


def test_unexpected_error_keeps_the_poller_alive(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    poller, _ = make_poller(store, empty_repo_routes(), Clock())

    def broken(entries: list[Entry]) -> None:
        raise ValueError("listener broke")

    poller.listeners.append(broken)
    assert poller.poll() == []
    assert poller.last_error == "listener broke"
    assert poller.polling is False
    assert "poll failed" in caplog.text


def test_first_poll_stays_silent_until_a_repository_was_read(store: Store) -> None:
    routes = {**empty_repo_routes(), "issue list": GhError("timeout", 504)}
    poller, runner = make_poller(store, routes, Clock())
    assert poller.poll() == []
    assert store.get_setting("notified_once") is None
    runner.routes["issue list"] = issues("2026-10-19T08:00:00Z")
    assert poller.poll() == []
    assert store.get_setting("notified_once") is not None


def test_no_repositories_ends_the_silent_first_poll(store: Store) -> None:
    poller = Poller(Config(), store, Gh(FakeGhRunner({"api user": {"login": "me"}})), Clock())
    poller.poll()
    assert store.get_setting("notified_once") is not None
