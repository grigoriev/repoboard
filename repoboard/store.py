"""SQLite storage: the latest snapshot of each repository and what the user has seen."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from repoboard.models import Access, AlertSource, Item, Kind, RepoSnapshot, Tag
from repoboard.triage import STATE_SECTIONS, Section, classify, marker

FIRST_RUN_LOOKBACK = timedelta(days=7)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    key TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    kind TEXT NOT NULL,
    number TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT NOT NULL,
    open INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    activity_at TEXT,
    activity_by TEXT,
    tags TEXT NOT NULL,
    renovate INTEGER NOT NULL,
    watched INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_repo ON items(repo);
CREATE TABLE IF NOT EXISTS repos (
    repo TEXT PRIMARY KEY,
    polled_at TEXT NOT NULL,
    error TEXT,
    open_discussions INTEGER NOT NULL,
    security TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marks (
    key TEXT PRIMARY KEY,
    seen_at TEXT,
    snoozed_until TEXT,
    notified_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Entry:
    """An item in the inbox, with the section it falls into and whether it changed since the user saw it."""

    item: Item
    section: Section
    marker: datetime
    unseen: bool
    snoozed_until: datetime | None = None


@dataclass(frozen=True)
class RepoRow:
    """One line of the overview."""

    repo: str
    polled_at: datetime | None
    error: str | None
    open_issues: int = 0
    open_discussions: int = 0
    open_prs: int = 0
    renovate: int = 0
    failed: int = 0
    alerts: int = 0
    unseen: int = 0
    last_activity: datetime | None = None
    security: dict[AlertSource, Access] = field(default_factory=dict)

    @property
    def security_readable(self) -> bool:
        return any(access is Access.OK for access in self.security.values())


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(items)")}
        if "watched" not in columns:  # a database from before watched items
            self._db.execute("ALTER TABLE items ADD COLUMN watched INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._db:
            yield self._db

    # settings

    def get_setting(self, name: str) -> str | None:
        with self._tx() as db:
            row = db.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, name: str, value: str) -> None:
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO settings(name, value) VALUES (?, ?)", (name, value))

    def baseline(self) -> datetime | None:
        return _dt(self.get_setting("baseline"))

    def ensure_baseline(self, now: datetime) -> datetime:
        """Event sections show only what happened after the baseline. The first run sets it a week back."""
        current = self.baseline()
        if current is None:
            current = now - FIRST_RUN_LOOKBACK
            self.set_setting("baseline", current.isoformat())
        return current

    # snapshots

    def save_snapshots(self, snapshots: Iterable[RepoSnapshot], now: datetime) -> None:
        with self._tx() as db:
            for snapshot in snapshots:
                if snapshot.error is None:
                    # Watched items have their own poll and stay. A repository item with the same key
                    # replaces it below, and the watched poll that follows marks it watched again.
                    db.execute("DELETE FROM items WHERE repo = ? AND watched = 0", (snapshot.repo,))
                    db.executemany(
                        "INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [self._item_row(item) for item in snapshot.items],
                    )
                    open_discussions, security = snapshot.open_discussions, json.dumps(snapshot.security)
                else:
                    previous = db.execute(
                        "SELECT open_discussions, security FROM repos WHERE repo = ?", (snapshot.repo,)
                    ).fetchone()
                    open_discussions = previous["open_discussions"] if previous else 0
                    security = previous["security"] if previous else "{}"
                db.execute(
                    "INSERT OR REPLACE INTO repos VALUES (?, ?, ?, ?, ?)",
                    (snapshot.repo, now.isoformat(), snapshot.error, open_discussions, security),
                )

    def save_watched(self, items: Iterable[Item], now: datetime, repos: set[str] | None = None) -> None:
        """Replace the watched items with the latest poll of them: all, or those of `repos`."""
        with self._tx() as db:
            previous = {row["key"]: row for row in db.execute("SELECT key, tags, open FROM items WHERE watched = 1")}
            if repos is None:
                db.execute("DELETE FROM items WHERE watched = 1")
            else:
                db.executemany("DELETE FROM items WHERE watched = 1 AND repo = ?", [(repo,) for repo in repos])
            rows = []
            for item in items:
                updated = item.updated_at
                before = previous.get(item.key)
                # GitHub does not touch updated_at for a failed check or a new conflict. Count the change
                # here, so the item comes back to the Watched section.
                if before and (before["tags"], bool(before["open"])) != (",".join(item.tags), item.open):
                    updated = max(updated, now)
                rows.append(self._item_row(item, watched=True, updated_at=updated))
            db.executemany("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def forget_repos_except(self, repos: Iterable[str]) -> None:
        keep = list(repos)
        marks = ",".join("?" * len(keep)) or "''"
        with self._tx() as db:
            db.execute(f"DELETE FROM items WHERE repo NOT IN ({marks}) AND watched = 0", keep)
            db.execute(f"DELETE FROM repos WHERE repo NOT IN ({marks})", keep)

    @staticmethod
    def _item_row(item: Item, watched: bool | None = None, updated_at: datetime | None = None) -> tuple[object, ...]:
        return (
            item.key,
            item.repo,
            item.kind.value,
            item.number,
            item.title,
            item.url,
            item.author,
            int(item.open),
            _ts(item.created_at),
            _ts(updated_at or item.updated_at),
            _ts(item.activity_at),
            item.activity_by,
            ",".join(item.tags),
            int(item.renovate),
            int(item.watched if watched is None else watched),
        )

    @staticmethod
    def _item(row: sqlite3.Row) -> Item:
        created = _dt(row["created_at"])
        updated = _dt(row["updated_at"])
        assert created and updated
        return Item(
            repo=row["repo"],
            kind=Kind(row["kind"]),
            number=row["number"],
            title=row["title"],
            url=row["url"],
            author=row["author"],
            open=bool(row["open"]),
            created_at=created,
            updated_at=updated,
            activity_at=_dt(row["activity_at"]),
            activity_by=row["activity_by"],
            tags=tuple(Tag(t) for t in row["tags"].split(",") if t),
            renovate=bool(row["renovate"]),
            watched=bool(row["watched"]),
        )

    def items(self, repo: str | None = None) -> list[Item]:
        with self._tx() as db:
            if repo is None:
                rows = db.execute("SELECT * FROM items").fetchall()
            else:
                rows = db.execute("SELECT * FROM items WHERE repo = ?", (repo,)).fetchall()
        return [self._item(row) for row in rows]

    # inbox

    def entries(self, now: datetime, repo: str | None = None) -> list[Entry]:
        """Every item that belongs to an inbox section, newest first, with its seen and snooze state."""
        baseline = self.baseline()
        with self._tx() as db:
            marks = {row["key"]: row for row in db.execute("SELECT * FROM marks").fetchall()}
        entries: list[Entry] = []
        for item in self.items(repo):
            section = classify(item)
            if section is None:
                continue
            when = marker(item, section)
            if section not in STATE_SECTIONS and baseline is not None and when <= baseline:
                continue
            mark = marks.get(item.key)
            seen_at = _dt(mark["seen_at"]) if mark else None
            snoozed = _dt(mark["snoozed_until"]) if mark else None
            entries.append(
                Entry(
                    item=item,
                    section=section,
                    marker=when,
                    unseen=seen_at is None or when > seen_at,
                    snoozed_until=snoozed if snoozed and snoozed > now else None,
                )
            )
        entries.sort(key=lambda e: e.marker, reverse=True)
        return entries

    def inbox(self, now: datetime) -> dict[Section, list[Entry]]:
        """Unseen, not snoozed entries grouped by section, in display order."""
        grouped: dict[Section, list[Entry]] = {section: [] for section in Section}
        for entry in self.entries(now):
            if entry.unseen and entry.snoozed_until is None:
                grouped[entry.section].append(entry)
        return grouped

    def mark_seen(self, keys: Iterable[str], now: datetime) -> None:
        """Record the marker the user saw, not the click time: activity between the last poll and the
        click has an older timestamp than the click, but the user has not seen it."""
        wanted = set(keys)
        markers = {e.item.key: e.marker for e in self.entries(now) if e.item.key in wanted}
        with self._tx() as db:
            db.executemany(
                "INSERT INTO marks(key, seen_at) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET seen_at = excluded.seen_at",
                [(key, markers.get(key, now).isoformat()) for key in wanted],
            )

    def mark_unseen(self, key: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE marks SET seen_at = NULL WHERE key = ?", (key,))

    def mark_all_seen(self, now: datetime) -> None:
        self.mark_seen([entry.item.key for entry in self.entries(now) if entry.unseen], now)

    def snooze(self, key: str, until: datetime | None) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO marks(key, snoozed_until) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET snoozed_until = excluded.snoozed_until",
                (key, _ts(until)),
            )

    def take_notifications(self, now: datetime, silent: bool = False) -> list[Entry]:
        """Return inbox entries that changed since their last notification, and record them as notified.

        `silent` records them without returning them: the first poll must not flood the screen.
        """
        with self._tx() as db:
            notified = {
                row["key"]: _dt(row["notified_at"])
                for row in db.execute("SELECT key, notified_at FROM marks").fetchall()
            }
        fresh = [
            entry
            for entries in self.inbox(now).values()
            for entry in entries
            if (last := notified.get(entry.item.key)) is None or entry.marker > last
        ]
        with self._tx() as db:
            db.executemany(
                "INSERT INTO marks(key, notified_at) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET notified_at = excluded.notified_at",
                [(entry.item.key, entry.marker.isoformat()) for entry in fresh],
            )
        return [] if silent else fresh

    # overview

    def overview(self, repos: Iterable[str], now: datetime) -> list[RepoRow]:
        with self._tx() as db:
            polled = {row["repo"]: row for row in db.execute("SELECT * FROM repos").fetchall()}
        items_by_repo: dict[str, list[Item]] = {}
        for item in self.items():
            items_by_repo.setdefault(item.repo, []).append(item)
        unseen: dict[str, int] = {}
        for entry in self.entries(now):
            if entry.unseen and entry.snoozed_until is None:
                unseen[entry.item.repo] = unseen.get(entry.item.repo, 0) + 1
        rows: list[RepoRow] = []
        for repo in repos:
            state = polled.get(repo)
            items = items_by_repo.get(repo, [])
            # A watched item counts by its state, as it would without the watch.
            sections = [classify(item, ignore_watch=True) for item in items]
            rows.append(
                RepoRow(
                    repo=repo,
                    polled_at=_dt(state["polled_at"]) if state else None,
                    error=state["error"] if state else None,
                    open_issues=sum(1 for i in items if i.kind is Kind.ISSUE and i.open),
                    open_discussions=state["open_discussions"] if state else 0,
                    open_prs=sum(1 for i in items if i.kind is Kind.PR and i.open),
                    renovate=sections.count(Section.RENOVATE),
                    failed=sections.count(Section.FAILED),
                    alerts=sections.count(Section.SECURITY),
                    unseen=unseen.get(repo, 0),
                    last_activity=max((i.updated_at for i in items if i.kind is not Kind.ALERT), default=None),
                    security={
                        AlertSource(k): Access(v) for k, v in json.loads(state["security"] if state else "{}").items()
                    },
                )
            )
        return rows
