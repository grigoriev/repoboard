"""The settings page: turn its form into a Config, save it and apply it without a restart."""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import TYPE_CHECKING
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


def form_to_config(form: Form, current: Config) -> Config:
    """Build the config the form describes. Port and database need a restart, so they stay as they are."""

    def one(name: str, default: str = "") -> str:
        return (form.get(name) or [default])[0]

    def column(name: str, index: int) -> str:
        values = form.get(name, [])
        return values[index] if index < len(values) else ""

    owners = []
    for index, name in enumerate(form.get("owner_name", [])):
        if not name.strip() or form.get(f"owner_remove_{index}"):
            continue
        owners.append(
            {
                "name": name.strip(),
                "include": list(words(column("owner_include", index))),
                "exclude": list(words(column("owner_exclude", index))),
                "exclude_types": form.get(f"owner_types_{index}", []),
            }
        )
    try:
        poll = int(one("poll_interval_minutes", str(current.poll_interval_minutes)))
        lookback = int(one("lookback_days", str(current.lookback_days)))
    except ValueError as exc:
        raise ConfigError(WHOLE_NUMBERS) from exc
    parsed = config.parse(
        {
            "repos": list(words(one("repos"))),
            "items": [line.strip() for line in one("items").splitlines() if line.strip()],
            "owners": owners,
            "language": one("language", current.language),
            "poll_interval_minutes": poll,
            "lookback_days": lookback,
            "hidden_alert_tools": [
                tool.strip() for tool in _LIST_SEPARATORS.split(one("hidden_alert_tools")) if tool.strip()
            ],
        }
    )
    return copy.replace(parsed, port=current.port, database=current.database)


async def read_multi_form(request: Request) -> Form:
    return parse_qs((await request.body()).decode(), keep_blank_values=True)


def apply(web: Web, updated: Config) -> None:
    """Save the config and hand it to the poller, which lists the owners again on its next poll."""
    config.save(updated)
    web.poller.config = updated
    web.poller.refresh()


def add_settings_page(app: FastAPI, web: Web) -> None:
    def page(request: Request, cfg: Config, error: str | None = None, status: int = 200) -> HTMLResponse:
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
            updated = form_to_config(form, web.poller.config)
        except ConfigError as exc:
            t = web.translator()
            reason = t(str(exc)) if str(exc) == WHOLE_NUMBERS else str(exc)
            return page(request, web.poller.config, error=t("settings.not_saved", error=reason), status=400)
        apply(web, updated)
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
            updated = edit(web.poller.config, url)
        except ConfigError as exc:
            return Response(str(exc), status_code=400)
        apply(web, updated)
        return Response(headers=UPDATED)
