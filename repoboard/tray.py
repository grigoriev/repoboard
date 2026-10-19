"""Menu bar icon: unseen count, inbox sections, refresh and quit. The model is plain Python; rumps draws it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from repoboard.i18n import Translator
from repoboard.poller import Poller
from repoboard.triage import Section

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
        labels={key: t(f"tray.{key}") for key in ("open", "refresh", "quit", "unseen")},
    )


def icon_path() -> str:
    return str(resources.files("repoboard") / "resources" / "tray.png")


def run_tray(
    poller: Poller, translator: Callable[[], Translator], open_window: Callable[[], None], on_quit: Callable[[], None]
) -> None:  # pragma: no cover - needs the macOS menu bar
    import rumps

    from repoboard.macos import brand_process

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
            self.menu.add(rumps.MenuItem(state.labels["unseen"]))
            for label, count in state.sections:
                self.menu.add(rumps.MenuItem(f"{label}: {count}", callback=lambda _: open_window()))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem(state.labels["open"], callback=lambda _: open_window(), key="o"))
            self.menu.add(rumps.MenuItem(state.labels["refresh"], callback=lambda _: poller.refresh(), key="r"))
            self.menu.add(rumps.MenuItem(state.status))
            self.menu.add(rumps.separator)
            self.menu.add(rumps.MenuItem(state.labels["quit"], callback=self.quit, key="q"))

        def quit(self, _: object) -> None:
            on_quit()
            rumps.quit_application()

    App().run()
