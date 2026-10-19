"""The settings page: turn its form into a Config, save it and apply it without a restart."""

from __future__ import annotations

import copy
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from repoboard import config
from repoboard.config import REPO_TYPES, Config, ConfigError, Owner
from repoboard.discovery import list_owner, matches
from repoboard.gh import GhError

if TYPE_CHECKING:
    from repoboard.web.app import Web

Form = dict[str, list[str]]
UPDATED = {"HX-Trigger": "repoboard-updated"}
_SEPARATORS = re.compile(r"[\s,]+")
_LIST_SEPARATORS = re.compile(r"[,\n]+")  # for names that may hold a space, such as "Semgrep OSS"
WHOLE_NUMBERS = "settings.whole_numbers"  # a catalog key: the page translates this error


def words(text: str) -> tuple[str, ...]:
    """Patterns or names typed with spaces, commas or line breaks between them."""
    return tuple(word for word in _SEPARATORS.split(text) if word)


@dataclass(frozen=True)
class Draft:
    """The posted form as typed, not validated: a rejected save shows it again, so no edit is lost."""

    owners: tuple[Owner, ...]
    repos: tuple[str, ...]
    items: tuple[str, ...]
    language: str
    poll_interval_minutes: str
    lookback_days: str
    hidden_alert_tools: tuple[str, ...]


def form_owners(form: Form) -> list[dict[str, Any]]:
    """The owner rows of the form that are filled in and not marked for removal."""

    def column(name: str, index: int) -> str:
        values = form.get(name, [])
        return values[index] if index < len(values) else ""

    return [
        {
            "name": name.strip(),
            "include": list(words(column("owner_include", index))),
            "exclude": list(words(column("owner_exclude", index))),
            "exclude_types": form.get(f"owner_types_{index}", []),
        }
        for index, name in enumerate(form.get("owner_name", []))
        if name.strip() and not form.get(f"owner_remove_{index}")
    ]


def form_tools(text: str) -> list[str]:
    return [tool.strip() for tool in _LIST_SEPARATORS.split(text) if tool.strip()]


def form_draft(form: Form, current: Config) -> Draft:
    def one(name: str, default: str = "") -> str:
        return (form.get(name) or [default])[0]

    owners = tuple(
        Owner(row["name"], tuple(row["include"]), tuple(row["exclude"]), tuple(row["exclude_types"]))
        for row in form_owners(form)
    )
    return Draft(
        owners=owners,
        repos=tuple(line.strip() for line in one("repos").splitlines() if line.strip()),
        items=tuple(line.strip() for line in one("items").splitlines() if line.strip()),
        language=one("language", current.language),
        poll_interval_minutes=one("poll_interval_minutes", str(current.poll_interval_minutes)),
        lookback_days=one("lookback_days", str(current.lookback_days)),
        hidden_alert_tools=tuple(form_tools(one("hidden_alert_tools"))),
    )


def form_to_config(form: Form, current: Config) -> Config:
    """Build the config the form describes. Port and database need a restart, so they stay as they are."""

    def one(name: str, default: str = "") -> str:
        return (form.get(name) or [default])[0]

    try:
        poll = int(one("poll_interval_minutes", str(current.poll_interval_minutes)))
        lookback = int(one("lookback_days", str(current.lookback_days)))
    except ValueError as exc:
        raise ConfigError(WHOLE_NUMBERS) from exc
    parsed = config.parse(
        {
            "repos": list(words(one("repos"))),
            "items": [line.strip() for line in one("items").splitlines() if line.strip()],
            "owners": form_owners(form),
            "language": one("language", current.language),
            "poll_interval_minutes": poll,
            "lookback_days": lookback,
            "hidden_alert_tools": form_tools(one("hidden_alert_tools")),
        }
    )
    return copy.replace(parsed, port=current.port, database=current.database)


async def read_multi_form(request: Request) -> Form:
    return parse_qs((await request.body()).decode(), keep_blank_values=True)


# Requests run in parallel. Each edit reads the config, changes it and saves it: one edit at a time.
EDITS = threading.Lock()


def apply(web: Web, edit: Callable[[Config], Config]) -> Config:
    """Edit the config, save it and hand it to the poller, which lists the owners again on its next poll."""
    with EDITS:
        updated = edit(web.poller.config)
        config.save(updated)
        web.poller.config = updated
    web.poller.refresh()
    return updated


def add_settings_page(app: FastAPI, web: Web) -> None:
    def page(
        request: Request, cfg: Config | Draft, error: str | None = None, status: int = 200, chosen: str | None = None
    ) -> HTMLResponse:
        response = web.render(
            request,
            "settings.html",
            page="settings",
            cfg=cfg,
            error=error,
            saved=request.query_params.get("saved") == "1",
            repo_types=REPO_TYPES,
            discovery_errors=web.poller.discovery_errors(),
            watched_errors=web.poller.watched_errors(),
            # render() sets `language` to the language in use; a rejected save shows the one picked instead.
            chosen_language=chosen,
        )
        response.status_code = status
        return response

    @app.get("/settings", response_class=HTMLResponse)
    def show(request: Request) -> HTMLResponse:
        return page(request, web.poller.config)

    @app.post("/settings", response_model=None)
    async def save(request: Request) -> Response:
        form = await read_multi_form(request)
        try:
            updated = apply(web, lambda current: form_to_config(form, current))
        except (ConfigError, OSError) as exc:
            t = web.translator()
            reason = t(str(exc)) if str(exc) == WHOLE_NUMBERS else str(exc)
            draft = form_draft(form, web.poller.config)
            return page(request, draft, error=t("settings.not_saved", error=reason), status=400, chosen=draft.language)
        # The page sets the language, so the choice of the header switcher must not override it.
        web.store.set_setting("language", updated.language)
        return RedirectResponse("/settings?saved=1", status_code=303)

    @app.get("/settings/preview", response_class=HTMLResponse)
    def preview(
        request: Request, owner: str, include: str = "", exclude: str = "", types: str = "archived fork"
    ) -> HTMLResponse:
        rule = Owner(owner, include=words(include), exclude=words(exclude), exclude_types=words(types))
        try:
            login = web.poller.gh.viewer_login()
            listed = list_owner(web.poller.gh, owner, login)
        except GhError as exc:
            return web.render(request, "settings_preview.html", owner=owner, error=str(exc), repos=[], total=0)
        chosen = [info.name for info in listed if matches(rule, info)]
        return web.render(request, "settings_preview.html", owner=owner, error=None, repos=chosen, total=len(listed))

    @app.post("/items/watch")
    async def watch(request: Request) -> Response:
        return change(config.watch, (await read_multi_form(request)).get("url", [""])[0])

    @app.post("/items/unwatch")
    async def unwatch(request: Request) -> Response:
        return change(config.unwatch, (await read_multi_form(request)).get("url", [""])[0])

    def change(edit: Callable[[Config, str], Config], url: str) -> Response:
        try:
            apply(web, lambda current: edit(current, url))
        except ConfigError as exc:
            return Response(str(exc), status_code=400)
        except OSError as exc:
            return Response(str(exc), status_code=500)
        return Response(headers=UPDATED)
