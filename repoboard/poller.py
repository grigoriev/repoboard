"""Poll the repositories on a timer, store the result and report what is new."""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from repoboard.collector import Collector
from repoboard.config import Config
from repoboard.discovery import list_owner, matches
from repoboard.gh import Gh, GhError
from repoboard.store import Entry, Store

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]
Listener = Callable[[list[Entry]], None]

# A repository the user opened an item of is "touched": it is polled again when the user comes back
# to Repoboard, and once more a little later, so a merge on GitHub shows up without a full poll.
TOUCH_TTL = timedelta(minutes=30)
# How long the repository list of the owners stays valid. New repositories show up after this at most.
DISCOVERY_TTL = timedelta(hours=1)
RepoListener = Callable[[list[str]], None]
FOLLOW_UP_SECONDS = 30.0


def utc_now() -> datetime:
    return datetime.now(UTC)


class Poller:
    """Runs one poll at a time. `start()` adds a background thread that polls every interval."""

    def __init__(self, config: Config, store: Store, gh: Gh, clock: Clock = utc_now) -> None:
        self.config = config
        self.store = store
        self.gh = gh
        self.clock = clock
        self.listeners: list[Listener] = []
        self.repo_listeners: list[RepoListener] = []  # told about repositories that appeared under an owner
        self.polling = False
        self.last_error: str | None = None
        self._me: str | None = None
        self._touched: dict[str, datetime] = {}
        self._pending: set[str] = set()
        self._follow_up: float | None = None  # time.monotonic() when the touched repositories poll again
        self._full_requested = False
        self._state = threading.Lock()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def repos(self) -> tuple[str, ...]:
        """The repositories to poll: the single ones of the config, plus what each owner last listed."""
        single = {repo.lower() for repo in self.config.repos}
        listed = {repo for entry in self._listings().values() for repo in entry["repos"]}
        return tuple(sorted([*self.config.repos, *(r for r in listed if r.lower() not in single)], key=str.lower))

    def _listings(self) -> dict[str, dict[str, Any]]:
        """The last listing of each owner of the config, by lower case name.

        An entry: "rule" (the owner's filters as they were listed), "at", "repos" and "error". An entry
        stays when its filters change, until the owner is listed again, so nothing flickers or is lost.
        """
        saved: dict[str, dict[str, Any]] = json.loads(self.store.get_setting("owner_listings") or "{}")
        names = {owner.name.lower() for owner in self.config.owners}
        return {name: entry for name, entry in saved.items() if name in names}

    def discovery_errors(self) -> dict[str, str]:
        listings = self._listings()
        return {
            owner.name: str(entry["error"])
            for owner in self.config.owners
            if (entry := listings.get(owner.name.lower())) and entry.get("error")
        }

    def _discover(self, now: datetime) -> None:
        listings = self._listings()
        single = {repo.lower() for repo in self.config.repos}
        added: list[str] = []
        for owner in self.config.owners:
            rule = json.dumps(dataclasses.astuple(owner))
            entry = listings.get(owner.name.lower())
            fresh = entry and entry["rule"] == rule and now - datetime.fromisoformat(entry["at"]) < DISCOVERY_TTL
            if fresh:
                continue
            assert self._me is not None
            try:
                listed = [info.name for info in list_owner(self.gh, owner.name, self._me) if matches(owner, info)]
            except GhError as exc:
                # The owner keeps what it had: dropping it would delete its data and announce it all later.
                kept = entry["repos"] if entry else []
                listings[owner.name.lower()] = {"rule": entry["rule"] if entry else "", "at": now.isoformat(),
                                                "repos": kept, "error": str(exc)}  # fmt: skip
                continue
            # News only under the same filters, after a good listing: not a first listing, not a wider filter.
            if entry and entry["rule"] == rule:
                known = {repo.lower() for repo in entry["repos"]} | single
                added += [repo for repo in listed if repo.lower() not in known]
            listings[owner.name.lower()] = {"rule": rule, "at": now.isoformat(), "repos": listed, "error": None}
        self.store.set_setting("owner_listings", json.dumps(listings))
        for listener in self.repo_listeners if added else ():
            listener(sorted(added, key=str.lower))

    def last_poll(self) -> datetime | None:
        value = self.store.get_setting("last_poll")
        return datetime.fromisoformat(value) if value else None

    def poll(self, repos: list[str] | None = None) -> list[Entry]:
        """Poll every repository, or only `repos`. Returns the entries to notify about.

        A poll already running wins: this one is skipped.
        """
        if not self._lock.acquire(blocking=False):
            return []
        self.polling = True
        try:
            return self._poll(repos)
        except GhError as exc:
            self.last_error = str(exc)
            log.warning("poll failed: %s", exc)
            return []
        except Exception as exc:  # an unexpected failure must not end the background loop
            self.last_error = str(exc) or type(exc).__name__
            log.exception("poll failed")
            return []
        finally:
            self.polling = False
            self._lock.release()

    def _poll(self, repos: list[str] | None) -> list[Entry]:
        now = self.clock()
        # Silent until one poll got as far as notifying. A failed first attempt must not lift it.
        first_run = self.store.get_setting("notified_once") is None
        self.store.ensure_baseline(now)
        if self._me is None:
            self._me = self.gh.viewer_login()
        since = now - timedelta(days=self.config.lookback_days)
        if repos is None:
            self._discover(now)
        watched = self.repos()
        targets = list(watched) if repos is None else [r for r in repos if r in watched]
        collector = Collector(self.gh, self._me, self.config.hidden_alert_tools)
        snapshots = collector.collect_all(targets, since)
        self.store.save_snapshots(snapshots, now)
        if repos is None:
            self.store.forget_repos_except(watched)
            self.store.set_setting("last_poll", now.isoformat())
        # The UI reloads its content whenever this changes: after a full or a partial poll.
        self.store.set_setting("data_version", f"{now.isoformat()}#{time.monotonic_ns()}")
        self.last_error = None
        if not self.listeners:
            return []  # leave the notifications to a process that delivers them, such as the tray
        fresh = self.store.take_notifications(now, silent=first_run)
        if any(snapshot.error is None for snapshot in snapshots):
            self.store.set_setting("notified_once", now.isoformat())
        for listener in self.listeners:
            listener(fresh)
        return fresh

    def refresh(self) -> None:
        """Ask the background thread to poll every repository now."""
        with self._state:
            self._full_requested = True
        self._wake.set()

    def touch(self, repo: str, now_monotonic: float | None = None) -> None:
        """The user opened an item of `repo`: poll it again in a moment."""
        due = (time.monotonic() if now_monotonic is None else now_monotonic) + FOLLOW_UP_SECONDS
        with self._state:
            self._touched[repo] = self.clock()
            self._follow_up = due if self._follow_up is None else min(self._follow_up, due)
        self._wake.set()

    def refresh_touched(self) -> None:
        """The user is back in Repoboard: poll the repositories they opened items of."""
        with self._state:
            self._pending.update(self._fresh_touched())
        self._wake.set()

    def _fresh_touched(self) -> list[str]:
        now = self.clock()
        self._touched = {repo: at for repo, at in self._touched.items() if now - at < TOUCH_TTL}
        return list(self._touched)

    def seconds_to_follow_up(self, now_monotonic: float | None = None) -> float | None:
        if self._follow_up is None:
            return None
        return max(self._follow_up - (time.monotonic() if now_monotonic is None else now_monotonic), 0.0)

    def take_pending(self, now_monotonic: float | None = None) -> list[str]:
        """Repositories to poll now: those asked for, plus the touched ones once their follow-up is due."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        with self._state:
            if self._follow_up is not None and now >= self._follow_up:
                self._follow_up = None
                self._pending.update(self._fresh_touched())
            pending, self._pending = sorted(self._pending), set()
        return pending

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="repoboard-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        interval = self.config.poll_interval_minutes * 60
        next_full = time.monotonic()
        while not self._stop.is_set():
            self._wake.clear()  # before the work: a wake during the work then ends the wait below at once
            with self._state:
                full, self._full_requested = self._full_requested or time.monotonic() >= next_full, False
            if full:
                self.poll()
                next_full = time.monotonic() + interval
            elif repos := self.take_pending():
                self.poll(repos)
            follow_up = self.seconds_to_follow_up()
            wait = next_full - time.monotonic()
            self._wake.wait(max(min(wait, follow_up if follow_up is not None else wait), 0.0))
