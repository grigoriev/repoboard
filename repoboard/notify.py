"""Desktop notifications about new inbox entries."""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from repoboard.i18n import Translator
from repoboard.store import Entry
from repoboard.triage import Section

log = logging.getLogger(__name__)

# More new entries than this in one poll give one summary notification instead of one each.
MAX_SINGLE = 3


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    url: str


class Notifier(Protocol):
    def send(self, notification: Notification) -> None: ...


def build(entries: Sequence[Entry], t: Translator, inbox_url: str) -> list[Notification]:
    if len(entries) > MAX_SINGLE:
        return [Notification(t("notify.summary_title", count=len(entries)), t("notify.summary_body"), inbox_url)]
    return [single(entry, t) for entry in entries]


def single(entry: Entry, t: Translator) -> Notification:
    item = entry.item
    repo = item.repo.split("/", 1)[1]
    if entry.section is Section.REPLIES:
        title = t("notify.reply", login=item.activity_by or "", repo=repo)
    elif entry.section is Section.NEW:
        title = t(f"notify.new.{item.kind}", repo=repo)  # one key per kind: the adjective follows its gender
    else:
        title = t(f"notify.{entry.section}", repo=repo)
    return Notification(title, item.title, item.url)


class DesktopNotifier:
    """Native notifications through desktop-notifier. A click opens the item."""

    def __init__(self, open_url: Callable[[str], object] = webbrowser.open) -> None:
        from desktop_notifier import DesktopNotifierSync
        from desktop_notifier.main import get_backend_class

        # Outside a macOS app bundle the library falls back to a backend that drops every notification.
        if get_backend_class().__name__ == "DummyNotificationCenter":
            raise RuntimeError("no notification backend outside an app bundle")
        self._notifier = DesktopNotifierSync(app_name="Repoboard")
        self._open_url = open_url

    def send(self, notification: Notification) -> None:
        self._notifier.send(
            title=notification.title,
            message=notification.body,
            on_clicked=lambda: self._open_url(notification.url),
        )


class OsascriptNotifier:
    """Fallback for macOS without an app bundle. A click does not open the item."""

    def send(self, notification: Notification) -> None:
        script = "display notification item 2 of argv with title item 1 of argv"
        subprocess.run(
            ["osascript", "-e", f"on run argv\n{script}\nend run", notification.title, notification.body],
            check=False,
            capture_output=True,
        )


class LogNotifier:
    def send(self, notification: Notification) -> None:
        log.info("%s: %s %s", notification.title, notification.body, notification.url)


def default_notifier() -> Notifier:  # pragma: no cover - depends on the desktop
    try:
        return DesktopNotifier()
    except Exception as exc:  # any backend failure falls back
        log.info("native notifications unavailable (%s), falling back", exc)
    return OsascriptNotifier() if sys.platform == "darwin" else LogNotifier()


class NotificationListener:
    """Poller listener: turns new entries into notifications, in the current language."""

    def __init__(self, notifier: Notifier, translator: Callable[[], Translator], inbox_url: str) -> None:
        self.notifier = notifier
        self.translator = translator
        self.inbox_url = inbox_url

    def __call__(self, entries: list[Entry]) -> None:
        for notification in build(entries, self.translator(), self.inbox_url):
            try:
                self.notifier.send(notification)
            except Exception:  # a broken notifier must not stop polling
                log.exception("notification failed")
