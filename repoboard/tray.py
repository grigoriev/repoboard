"""Tray icon: unseen count, inbox sections, refresh and quit.

The model is plain Python. rumps draws it in the macOS menu bar, pystray in the Windows and Linux tray.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Any

from repoboard.i18n import Translator
from repoboard.poller import Poller
from repoboard.triage import Section

log = logging.getLogger(__name__)

REFRESH_SECONDS = 5


@dataclass(frozen=True)
class TrayState:
    title: str
    sections: tuple[tuple[str, int], ...]
    status: str
    labels: dict[str, str]


def tray_state(poller: Poller, t: Translator) -> TrayState:
    now = poller.clock()
    inbox = poller.store.inbox(now)
    total = sum(len(entries) for entries in inbox.values())
    last = poller.last_poll()
    if poller.polling:
        status = t("tray.polling")
    elif last:
        status = t("tray.last_poll", time=t.age(last, now))
    else:
        status = t("toolbar.never_polled")
    if poller.last_error:
        status = f"{status} · {poller.last_error[:60]}"
    return TrayState(
        title=str(total) if total else "",
        sections=tuple((t(f"section.{section}"), len(inbox[section])) for section in Section),
        status=status,
        labels={key: t(f"tray.{key}") for key in ("open", "refresh", "quit", "unseen", "open_at_login")},
    )


Entry = tuple[str, str | None]  # label and action: "open", "refresh", "quit", or None for plain text
SEPARATOR: Entry = ("-", None)
BADGE_SIZE = 64


def menu_entries(state: TrayState) -> list[Entry]:
    return [
        (state.labels["unseen"], None),
        *((f"{label}: {count}", "open") for label, count in state.sections),
        SEPARATOR,
        (state.labels["open"], "open"),
        (state.labels["refresh"], "refresh"),
        (state.status, None),
        SEPARATOR,
        (state.labels["quit"], "quit"),
    ]


def icon_path() -> str:
    return str(resources.files("repoboard") / "resources" / "tray.png")


def badge_icon(count: str) -> Any:
    """The app icon with the unseen count in a red badge. Tray icons on Windows and Linux show no text."""
    from PIL import Image, ImageDraw, ImageFont

    path = resources.files("repoboard") / "resources" / "icon.png"
    image = Image.open(str(path)).convert("RGBA").resize((BADGE_SIZE, BADGE_SIZE))
    if count:
        draw = ImageDraw.Draw(image)
        draw.ellipse((28, 0, 63, 35), fill=(197, 48, 48, 255))
        font = ImageFont.load_default(size=22 if len(count) < 3 else 16)
        draw.text((45.5, 17.5), count, fill="white", font=font, anchor="mm")
    return image


def run_tray(
    poller: Poller, translator: Callable[[], Translator], open_window: Callable[[], None], on_quit: Callable[[], None]
) -> None:  # pragma: no cover - needs a desktop tray
    if sys.platform == "darwin":
        run_menu_bar(poller, translator, open_window, on_quit)
    else:
        run_system_tray(poller, translator, open_window, on_quit)


def linux_tray_hint() -> str | None:
    """Without PyGObject pystray falls back to its xorg backend, which shows no menu on Linux."""
    if sys.platform.startswith("linux") and importlib.util.find_spec("gi") is None:
        return "the tray has no menu without PyGObject: install repoboard with the linux extra"
    return None


def run_system_tray(
    poller: Poller, translator: Callable[[], Translator], open_window: Callable[[], None], on_quit: Callable[[], None]
) -> None:  # pragma: no cover - needs the Windows or Linux tray
    import pystray

    hint = linux_tray_hint()
    if hint:
        log.warning(hint)
    actions = {"open": open_window, "refresh": poller.refresh}
    stopped = threading.Event()

    def quit_app(icon: Any) -> None:
        stopped.set()
        on_quit()
        icon.stop()

    def item(label: str, action: str | None) -> Any:
        if label == SEPARATOR[0]:
            return pystray.Menu.SEPARATOR
        if action == "quit":
            return pystray.MenuItem(label, lambda icon, _: quit_app(icon))
        if action:
            run = actions[action]
            return pystray.MenuItem(label, lambda _icon, _item: run(), default=action == "open")
        return pystray.MenuItem(label, None, enabled=False)

    def items() -> Any:
        return [item(label, action) for label, action in menu_entries(tray_state(poller, translator()))]

    def refresh(icon: Any) -> None:
        icon.visible = True
        while not stopped.wait(REFRESH_SECONDS if icon.visible else 0):
            state = tray_state(poller, translator())
            icon.icon = badge_icon(state.title)
            icon.title = f"Repoboard {state.title}".strip()
            icon.update_menu()

    pystray.Icon("repoboard", badge_icon(""), "Repoboard", menu=pystray.Menu(items)).run(setup=refresh)


def run_menu_bar(
    poller: Poller, translator: Callable[[], Translator], open_window: Callable[[], None], on_quit: Callable[[], None]
) -> None:  # pragma: no cover - needs the macOS menu bar
    import rumps

    from repoboard.macos import brand_process, in_app_bundle, opens_at_login, set_open_at_login

    brand_process(menu_bar_only=True)

    class App(rumps.App):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__("Repoboard", icon=icon_path(), template=True, quit_button=None)
            self.signature: TrayState | None = None
            self.timer = rumps.Timer(self.update, REFRESH_SECONDS)
            self.timer.start()
            self.update(None)

        def update(self, _: object) -> None:
            state = tray_state(poller, translator())
            self.title = state.title
            if state == self.signature:
                return
            self.signature = state
            self.menu.clear()
            callbacks = {"open": lambda _: open_window(), "refresh": lambda _: poller.refresh(), "quit": self.quit}
            keys = {state.labels["open"]: "o", state.labels["refresh"]: "r", state.labels["quit"]: "q"}
            for label, action in menu_entries(state):
                if label == SEPARATOR[0]:
                    self.menu.add(rumps.separator)
                    continue
                if action == "quit" and in_app_bundle():
                    login = rumps.MenuItem(state.labels["open_at_login"], callback=self.toggle_login)
                    login.state = int(opens_at_login())
                    self.menu.add(login)
                callback = callbacks.get(action or "")
                self.menu.add(rumps.MenuItem(label, callback=callback, key=keys.get(label) if callback else None))

        def toggle_login(self, item: rumps.MenuItem) -> None:
            set_open_at_login(not item.state)
            item.state = int(opens_at_login())

        def quit(self, _: object) -> None:
            on_quit()
            rumps.quit_application()

    App().run()
