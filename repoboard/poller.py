"""Poll the repositories on a timer, store the result and report what is new."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from repoboard.collector import Collector
from repoboard.config import Config
from repoboard.gh import Gh, GhError
from repoboard.store import Entry, Store

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]
Listener = Callable[[list[Entry]], None]


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
        self.polling = False
        self.last_error: str | None = None
        self._me: str | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def last_poll(self) -> datetime | None:
        value = self.store.get_setting("last_poll")
        return datetime.fromisoformat(value) if value else None

    def poll(self) -> list[Entry]:
        """Poll every repository now. Returns the entries to notify about. A poll already running wins."""
        if not self._lock.acquire(blocking=False):
            return []
        self.polling = True
        try:
            return self._poll()
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

    def _poll(self) -> list[Entry]:
        now = self.clock()
        # Silent until one poll got as far as notifying. A failed first attempt must not lift it.
        first_run = self.store.get_setting("notified_once") is None
        self.store.ensure_baseline(now)
        if self._me is None:
            self._me = self.gh.viewer_login()
        since = now - timedelta(days=self.config.lookback_days)
        snapshots = Collector(self.gh, self._me).collect_all(list(self.config.repos), since)
        self.store.save_snapshots(snapshots, now)
        self.store.forget_repos_except(self.config.repos)
        self.store.set_setting("last_poll", now.isoformat())
        self.last_error = None
        fresh = self.store.take_notifications(now, silent=first_run)
        self.store.set_setting("notified_once", now.isoformat())
        for listener in self.listeners:
            listener(fresh)
        return fresh

    def refresh(self) -> None:
        """Ask the background thread to poll now."""
        self._wake.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="repoboard-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        interval = self.config.poll_interval_minutes * 60
        while not self._stop.is_set():
            self.poll()
            self._wake.wait(interval)
            self._wake.clear()
