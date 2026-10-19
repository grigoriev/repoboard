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
    poller.listeners.append(lambda entries: None)
    runner.routes["issue list"] = issues("2026-10-19T08:00:00Z")
    assert poller.poll() == []
    assert store.get_setting("notified_once") is not None


def test_no_repositories_keeps_the_first_poll_silent(store: Store) -> None:
    poller = Poller(Config(), store, Gh(FakeGhRunner({"api user": {"login": "me"}})), Clock())
    poller.poll()
    assert store.get_setting("notified_once") is None


def test_poll_without_listeners_leaves_notifications_to_the_tray(store: Store) -> None:
    routes = {**empty_repo_routes(), "issue list": issues("2026-10-19T08:00:00Z")}
    poller, _ = make_poller(store, routes, Clock())
    poller.poll()
    assert store.get_setting("notified_once") is None
    assert len(store.take_notifications(NOW)) == 1


def two_repo_poller(store: Store, clock: Clock) -> tuple[Poller, FakeGhRunner]:
    runner = FakeGhRunner({"api user": {"login": "me"}, **empty_repo_routes()})
    return Poller(Config(repos=("o/a", "o/b")), store, Gh(runner), clock), runner


def polled_repos(runner: FakeGhRunner) -> set[str]:
    return {call[call.index("-R") + 1] for call in runner.calls if "-R" in call}


def test_poll_of_some_repositories(store: Store) -> None:
    clock = Clock()
    poller, runner = two_repo_poller(store, clock)
    poller.poll()
    runner.calls.clear()
    before = store.get_setting("data_version")
    poller.poll(["o/b"])
    assert polled_repos(runner) == {"o/b"}
    assert store.get_setting("data_version") != before
    assert poller.last_poll() == NOW


def test_touched_repositories_poll_when_the_user_returns(store: Store) -> None:
    clock = Clock()
    poller, runner = two_repo_poller(store, clock)
    assert poller.take_pending() == []
    poller.touch("o/a")
    poller.refresh_touched()
    assert poller.take_pending() == ["o/a"]
    assert poller.take_pending() == []
    clock.now = NOW + timedelta(hours=1)
    poller.refresh_touched()
    assert poller.take_pending() == []


def test_touch_schedules_a_follow_up(store: Store) -> None:
    poller, _ = two_repo_poller(store, Clock())
    assert poller.seconds_to_follow_up(0.0) is None
    poller.touch("o/b", now_monotonic=100.0)
    assert poller.seconds_to_follow_up(110.0) == 20.0
    assert poller.take_pending(now_monotonic=110.0) == []
    assert poller.take_pending(now_monotonic=131.0) == ["o/b"]
    assert poller.seconds_to_follow_up(131.0) is None


def test_background_thread_polls_touched_repositories(store: Store) -> None:
    clock = Clock()
    poller, runner = two_repo_poller(store, clock)
    polled = threading.Event()
    poller.listeners.append(lambda entries: polled.set() if polled_repos(runner) == {"o/a"} else None)
    poller.start()
    try:
        deadline = 50
        while poller.last_poll() is None and deadline:
            threading.Event().wait(0.1)
            deadline -= 1
        runner.calls.clear()
        poller.touch("o/a")
        poller.refresh_touched()
        assert polled.wait(5)
    finally:
        poller.stop()


def owner_repo(name: str, **flags: Any) -> dict[str, Any]:
    return {"full_name": name, "archived": False, "fork": False, "mirror_url": None, "is_template": False,
            "private": False, **flags}  # fmt: skip


def test_owners_are_discovered_and_new_repositories_reported(store: Store) -> None:
    from repoboard.config import Owner

    clock = Clock()
    listing = [[owner_repo("acme/a"), owner_repo("acme/old", archived=True)]]
    runner = FakeGhRunner({"api user": {"login": "me"}, "orgs/acme/repos": lambda call: listing, **empty_repo_routes()})
    poller = Poller(Config(repos=("cli/cli",), owners=(Owner("acme"),)), store, Gh(runner), clock)
    added: list[list[str]] = []
    poller.repo_listeners.append(added.append)
    assert poller.repos() == ("cli/cli",)
    poller.poll()
    assert poller.repos() == ("acme/a", "cli/cli")
    assert polled_repos(runner) == {"acme/a", "cli/cli"}
    assert added == []  # the first discovery reports nothing
    listing[0].append(owner_repo("acme/b"))
    clock.now = NOW + timedelta(minutes=30)
    poller.poll()
    assert "acme/b" not in poller.repos()  # the listing is cached for an hour
    clock.now = NOW + timedelta(hours=2)
    poller.poll()
    assert poller.repos() == ("acme/a", "acme/b", "cli/cli")
    assert added == [["acme/b"]]


def test_discovery_errors_are_kept(store: Store) -> None:
    from repoboard.config import Owner

    runner = FakeGhRunner({"api user": {"login": "me"}, "orgs/gone/repos": GhError("boom", 500), **empty_repo_routes()})
    poller = Poller(Config(owners=(Owner("gone"),)), store, Gh(runner), Clock())
    poller.poll()
    assert poller.discovery_errors() == {"gone": "boom"}
    assert poller.repos() == ()


def test_partial_poll_accepts_discovered_repositories(store: Store) -> None:
    from repoboard.config import Owner

    runner = FakeGhRunner(
        {"api user": {"login": "me"}, "orgs/acme/repos": [[owner_repo("acme/a")]], **empty_repo_routes()}
    )
    poller = Poller(Config(owners=(Owner("acme"),)), store, Gh(runner), Clock())
    poller.poll()
    runner.calls.clear()
    poller.poll(["acme/a", "not/watched"])
    assert polled_repos(runner) == {"acme/a"}


def test_changed_owners_drop_the_cached_listing(store: Store) -> None:
    from repoboard.config import Owner

    runner = FakeGhRunner(
        {"api user": {"login": "me"}, "orgs/acme/repos": [[owner_repo("acme/a"), owner_repo("acme/b")]],
         **empty_repo_routes()}
    )  # fmt: skip
    poller = Poller(Config(owners=(Owner("acme"),)), store, Gh(runner), Clock())
    poller.poll()
    assert poller.repos() == ("acme/a", "acme/b")
    poller.config = Config(owners=(Owner("acme", exclude=("b",)),))
    assert poller.repos() == ()  # the old listing no longer applies
    poller.poll()
    assert poller.repos() == ("acme/a",)
