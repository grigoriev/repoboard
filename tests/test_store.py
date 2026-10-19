from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repoboard.models import Access, AlertSource, Item, Kind, RepoSnapshot, Tag
from repoboard.store import Store
from repoboard.triage import Section

NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)
HOUR = timedelta(hours=1)


@pytest.fixture
def store() -> Iterator[Store]:
    s = Store(":memory:")
    yield s
    s.close()


def item(number: str, kind: Kind = Kind.ISSUE, repo: str = "o/r", **changes: object) -> Item:
    at = NOW - HOUR
    base = Item(
        repo=repo,
        kind=kind,
        number=number,
        title=f"T{number}",
        url=f"https://github.com/{repo}/{number}",
        author="alice",
        open=True,
        created_at=at,
        updated_at=at,
        activity_at=at,
        activity_by="alice",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_creates_parent_directory(tmp_path: Path) -> None:
    s = Store(tmp_path / "a" / "db.sqlite3")
    s.set_setting("x", "1")
    s.close()
    reopened = Store(tmp_path / "a" / "db.sqlite3")
    assert reopened.get_setting("x") == "1"
    reopened.close()


def test_baseline_is_set_once(store: Store) -> None:
    assert store.baseline() is None
    first = store.ensure_baseline(NOW)
    assert first == NOW - timedelta(days=7)
    assert store.ensure_baseline(NOW + HOUR) == first


def test_round_trip_items(store: Store) -> None:
    original = item("5", Kind.PR, tags=(Tag.CHECKS_FAILURE, Tag.CONFLICT), renovate=True, activity_at=None)
    store.save_snapshots([RepoSnapshot("o/r", (original,))], NOW)
    assert store.items() == [original]
    assert store.items("o/other") == []


def test_snapshot_replaces_items_and_keeps_them_on_error(store: Store) -> None:
    store.save_snapshots(
        [RepoSnapshot("o/r", (item("1"), item("2")), open_discussions=4, security={AlertSource.DEPENDABOT: Access.OK})],
        NOW,
    )
    store.save_snapshots([RepoSnapshot("o/r", (item("2"),), open_discussions=3)], NOW)
    assert [i.number for i in store.items()] == ["2"]
    store.save_snapshots([RepoSnapshot("o/r", error="boom")], NOW)
    assert [i.number for i in store.items()] == ["2"]
    (row,) = store.overview(["o/r"], NOW)
    assert row.error == "boom"
    assert row.open_discussions == 3


def test_error_on_first_poll(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/new", error="gone")], NOW)
    (row,) = store.overview(["o/new"], NOW)
    assert row.error == "gone"
    assert row.security == {}


def test_inbox_sections_and_baseline(store: Store) -> None:
    store.ensure_baseline(NOW)
    old = NOW - timedelta(days=30)
    items = (
        item("1"),
        item("2", created_at=old, activity_at=NOW - HOUR, activity_by="bob"),
        item("3", created_at=old, updated_at=old, activity_at=old),
        item("4", Kind.PR, renovate=True, activity_at=None, tags=(Tag.REVIEW_REQUIRED,), updated_at=old),
        item("dependabot/1", Kind.ALERT),
        item("dependabot/2", Kind.ALERT, created_at=old, updated_at=old, activity_at=old),
    )
    store.save_snapshots([RepoSnapshot("o/r", items)], NOW)
    inbox = store.inbox(NOW)
    assert [e.item.number for e in inbox[Section.NEW]] == ["1"]
    assert [e.item.number for e in inbox[Section.REPLIES]] == ["2"]
    assert [e.item.number for e in inbox[Section.RENOVATE]] == ["4"]
    assert [e.item.number for e in inbox[Section.SECURITY]] == ["dependabot/1", "dependabot/2"]
    assert inbox[Section.FAILED] == []


def test_seen_hides_until_new_activity(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/r", (item("1"),))], NOW)
    key = "o/r#issue#1"
    store.mark_seen([key], NOW)
    assert store.inbox(NOW)[Section.NEW] == []
    (entry,) = store.entries(NOW)
    assert entry.unseen is False
    reply = item("1", activity_at=NOW + HOUR, activity_by="bob")
    store.save_snapshots([RepoSnapshot("o/r", (reply,))], NOW + HOUR)
    assert [e.item.number for e in store.inbox(NOW + HOUR)[Section.REPLIES]] == ["1"]
    store.mark_seen([key], NOW + 2 * HOUR)
    store.mark_unseen(key)
    assert len(store.inbox(NOW + 2 * HOUR)[Section.REPLIES]) == 1


def test_mark_all_seen(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/r", (item("1"), item("2")))], NOW)
    store.mark_all_seen(NOW)
    assert all(not entries for entries in store.inbox(NOW).values())


def test_snooze(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/r", (item("1"),))], NOW)
    store.snooze("o/r#issue#1", NOW + HOUR)
    assert store.inbox(NOW)[Section.NEW] == []
    (entry,) = store.entries(NOW)
    assert entry.snoozed_until == NOW + HOUR
    assert len(store.inbox(NOW + 2 * HOUR)[Section.NEW]) == 1
    store.snooze("o/r#issue#1", None)
    assert len(store.inbox(NOW)[Section.NEW]) == 1


def test_take_notifications(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/r", (item("1"),))], NOW)
    assert store.take_notifications(NOW, silent=True) == []
    store.save_snapshots([RepoSnapshot("o/r", (item("1"), item("2", activity_at=NOW)))], NOW)
    assert [e.item.number for e in store.take_notifications(NOW)] == ["2"]
    assert store.take_notifications(NOW) == []
    store.save_snapshots([RepoSnapshot("o/r", (item("1", activity_at=NOW + HOUR, activity_by="bob"),))], NOW)
    assert [e.item.number for e in store.take_notifications(NOW + HOUR)] == ["1"]


def test_overview_counts(store: Store) -> None:
    items = (
        item("1"),
        item("2", open=False, activity_at=None),
        item("3", Kind.PR, renovate=True, activity_at=None, tags=(Tag.NO_AUTOMERGE,)),
        item("4", Kind.PR, tags=(Tag.CHECKS_FAILURE,), updated_at=NOW),
        item("code_scanning/1", Kind.ALERT, updated_at=NOW + HOUR),
    )
    security = {AlertSource.DEPENDABOT: Access.NO_ACCESS, AlertSource.CODE_SCANNING: Access.OK}
    store.save_snapshots([RepoSnapshot("o/r", items, open_discussions=2, security=security)], NOW)
    store.snooze("o/r#issue#1", NOW + HOUR)
    row, missing = store.overview(["o/r", "o/missing"], NOW)
    assert (row.open_issues, row.open_discussions, row.open_prs) == (1, 2, 2)
    assert (row.renovate, row.failed, row.alerts, row.unseen) == (1, 1, 1, 3)
    assert row.last_activity == NOW
    assert row.polled_at == NOW
    assert row.security_readable
    assert missing.polled_at is None
    assert not missing.security_readable


def test_forget_repos_except(store: Store) -> None:
    store.save_snapshots(
        [RepoSnapshot("o/a", (item("1", repo="o/a"),)), RepoSnapshot("o/b", (item("1", repo="o/b"),))], NOW
    )
    store.forget_repos_except(["o/a"])
    assert {i.repo for i in store.items()} == {"o/a"}
    store.forget_repos_except([])
    assert store.items() == []


def test_reply_before_the_click_but_after_the_poll_is_not_lost(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/r", (item("1"),))], NOW)
    reply_at = NOW + HOUR
    store.mark_seen(["o/r#issue#1"], NOW + 2 * HOUR)
    store.save_snapshots([RepoSnapshot("o/r", (item("1", activity_at=reply_at, activity_by="bob"),))], NOW + 3 * HOUR)
    assert [e.item.number for e in store.inbox(NOW + 3 * HOUR)[Section.REPLIES]] == ["1"]


def test_mark_seen_of_an_unknown_key_uses_the_click_time(store: Store) -> None:
    store.mark_seen(["o/r#issue#404"], NOW)
    store.save_snapshots([RepoSnapshot("o/r", (item("404"),))], NOW)
    assert store.inbox(NOW)[Section.NEW] == []


def test_watched_items_survive_their_repository_snapshot(store: Store) -> None:
    watched = item("7", Kind.PR, repo="cli/cli", watched=True, tags=(Tag.MERGED,), open=False)
    store.save_watched([watched], NOW)
    store.save_snapshots([RepoSnapshot("cli/cli", (item("1", repo="cli/cli"),))], NOW)
    assert {i.number: i.watched for i in store.items("cli/cli")} == {"1": False, "7": True}
    store.save_watched([], NOW)
    assert [i.number for i in store.items("cli/cli")] == ["1"]
    assert [e.item.number for e in store.inbox(NOW)[Section.WATCHED]] == []


def test_watched_item_in_a_watched_repository(store: Store) -> None:
    store.save_snapshots([RepoSnapshot("o/r", (item("1"),))], NOW)
    store.save_watched([item("1", watched=True)], NOW)
    store.save_snapshots([RepoSnapshot("o/r", (item("1"),))], NOW)
    store.save_watched([item("1", watched=True)], NOW)
    (only,) = store.items("o/r")
    assert only.watched
    assert [e.section for e in store.entries(NOW)] == [Section.WATCHED]


def test_old_database_gets_the_watched_column(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE items (key TEXT PRIMARY KEY, repo TEXT NOT NULL, kind TEXT NOT NULL, number TEXT NOT NULL,"
        " title TEXT NOT NULL, url TEXT NOT NULL, author TEXT NOT NULL, open INTEGER NOT NULL,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, activity_at TEXT, activity_by TEXT,"
        " tags TEXT NOT NULL, renovate INTEGER NOT NULL)"
    )
    db.commit()
    db.close()
    migrated = Store(path)
    migrated.save_watched([item("1", watched=True)], NOW)
    assert migrated.items()[0].watched
    migrated.close()


def test_forgetting_repositories_keeps_watched_items(store: Store) -> None:
    store.save_watched([item("7", repo="cli/cli")], NOW)
    store.forget_repos_except([])
    assert [i.number for i in store.items()] == ["7"]


def test_save_watched_of_some_repositories(store: Store) -> None:
    store.save_watched([item("1", repo="a/a"), item("2", repo="b/b")], NOW)
    store.save_watched([item("3", repo="a/a")], NOW, repos={"a/a"})
    assert sorted(i.key for i in store.items()) == ["a/a#issue#3", "b/b#issue#2"]


def test_a_watched_item_comes_back_when_its_state_changes(store: Store) -> None:
    pr = item("9", Kind.PR, repo="cli/cli")
    store.save_watched([pr], NOW)
    store.mark_seen([pr.key], NOW)
    assert store.inbox(NOW)[Section.WATCHED] == []
    store.save_watched([pr], NOW + HOUR)  # nothing changed
    assert store.inbox(NOW + HOUR)[Section.WATCHED] == []
    store.save_watched([replace(pr, tags=(Tag.CHECKS_FAILURE,))], NOW + 2 * HOUR)
    assert [e.item.number for e in store.inbox(NOW + 2 * HOUR)[Section.WATCHED]] == ["9"]


def test_overview_counts_watched_pull_requests_by_their_state(store: Store) -> None:
    items = (item("1", Kind.PR, tags=(Tag.CHECKS_FAILURE,)),)
    store.save_snapshots([RepoSnapshot("o/r", items)], NOW)
    store.save_watched(
        [item("1", Kind.PR, tags=(Tag.CHECKS_FAILURE,)), item("2", Kind.PR, open=False, tags=(Tag.MERGED,))], NOW
    )
    (row,) = store.overview(["o/r"], NOW)
    assert (row.open_prs, row.failed) == (1, 1)
