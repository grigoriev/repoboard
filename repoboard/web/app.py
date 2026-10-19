"""Local web UI: the inbox, the overview and a page per repository. Served on 127.0.0.1 only."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from importlib import resources
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel

from repoboard import window
from repoboard.config import LANGUAGES, config_path
from repoboard.i18n import LANGUAGE_NAMES, Translator
from repoboard.models import Item, Kind
from repoboard.poller import Poller
from repoboard.store import RepoRow
from repoboard.triage import Section

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
# Tells the page to reload its content in place, after an action or when a poll brought new data.
UPDATED = {"HX-Trigger": "repoboard-updated"}
KIND_ORDER = (Kind.ALERT, Kind.PR, Kind.ISSUE, Kind.DISCUSSION)
SNOOZE_DAYS = (1, 7)
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 2.0, 0.1
WINDOW_DEFAULT = {"width": window.WIDTH, "height": window.HEIGHT}
WINDOW_LIMITS = {"width": (400, 8000), "height": (300, 8000)}
# Requests that a browser marks as coming from another site, such as an <img> on a foreign page.
FOREIGN_SITES = frozenset({"cross-site", "same-site"})

# Overview columns: the sort key of a row, and the direction of the first click.
SORT_COLUMNS: dict[str, tuple[Callable[[RepoRow], Any], str]] = {
    "repo": (lambda row: row.repo.lower(), "asc"),
    "unseen": (lambda row: row.unseen, "desc"),
    "issues": (lambda row: row.open_issues, "desc"),
    "discussions": (lambda row: row.open_discussions, "desc"),
    "prs": (lambda row: row.open_prs, "desc"),
    "renovate": (lambda row: row.renovate, "desc"),
    "failed": (lambda row: row.failed, "desc"),
    "alerts": (lambda row: row.alerts, "desc"),
    "activity": (lambda row: row.last_activity, "desc"),
}


class WindowSize(BaseModel):
    width: int | None = None
    height: int | None = None


def sort_rows(rows: list[RepoRow], column: str, direction: str) -> list[RepoRow]:
    """Sort by one column. Rows without a value, such as a repository never polled, go last."""
    key = SORT_COLUMNS[column][0]
    present = [row for row in rows if key(row) is not None]
    missing = [row for row in rows if key(row) is None]
    present.sort(key=lambda row: row.repo.lower())  # ties stay alphabetical: the next sort is stable
    present.sort(key=key, reverse=direction == "desc")
    return present + missing


_web = resources.files("repoboard") / "web"
templates = Jinja2Templates(directory=str(_web / "templates"))


def item_ref(item: Item) -> str:
    """'#12' for issues and PRs, 'D17' for discussions, the scanning tool for alerts."""
    if item.kind is Kind.DISCUSSION:
        return f"D{item.number}"
    if item.kind is Kind.ALERT:
        return item.author
    return f"#{item.number}"


def short_repo(repo: str) -> Markup:
    """Render 'owner/name' with a dimmed owner, so the name stands out in long lists."""
    owner, name = repo.split("/", 1)
    return Markup('<span class="owner">{}/</span><wbr>{}').format(owner, name)


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def by_org[T](values: list[T], org: str | None, repo: Callable[[T], str]) -> list[T]:
    return [value for value in values if repo(value).startswith(f"{org}/")] if org else values


def parse_sort(saved: str | None) -> tuple[str, str]:
    """The saved overview sort as (column, direction), or the name sort when it is missing or broken."""
    column, _, direction = (saved or "").partition(" ")
    if column in SORT_COLUMNS and direction in ("asc", "desc"):
        return column, direction
    return "repo", "asc"


def sort_link(name: str, column: str, direction: str, org: str | None) -> str:
    """Link of a column header: the same column flips the direction, another one starts with its default."""
    flipped = {"asc": "desc", "desc": "asc"}[direction] if name == column else SORT_COLUMNS[name][1]
    query = {"sort": name, "dir": flipped, **({"org": org} if org else {})}
    return f"/overview?{urlencode(query)}"


async def read_form(request: Request) -> dict[str, str]:
    parsed = parse_qs((await request.body()).decode())
    return {key: values[0] for key, values in parsed.items()}


class Web:
    """State the routes share: the poller, its store and config, and the page rendering."""

    def __init__(self, poller: Poller) -> None:
        self.poller = poller
        self.store = poller.store
        self.config = poller.config

    def translator(self) -> Translator:
        return Translator(self.store.get_setting("language") or self.config.language)

    def zoom(self) -> float:
        try:
            value = float(self.store.get_setting("zoom") or 1.0)
        except ValueError:
            value = 1.0
        return clamp(value, ZOOM_MIN, ZOOM_MAX) if math.isfinite(value) else 1.0

    def window_size(self) -> dict[str, int]:
        try:
            saved = json.loads(self.store.get_setting("window") or "{}")
        except ValueError:
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        return {key: value if isinstance(value := saved.get(key), int) else default
                for key, default in WINDOW_DEFAULT.items()}  # fmt: skip

    def data_version(self) -> str:
        return self.store.get_setting("data_version") or "never"

    def owners(self) -> list[str]:
        return sorted({repo.split("/", 1)[0] for repo in self.poller.repos()}, key=str.lower)

    def render(self, request: Request, name: str, **context: Any) -> HTMLResponse:
        t = self.translator()
        now = self.poller.clock()
        context.update(
            t=t,
            now=now,
            age=lambda then: t.age(then, now),
            ref=item_ref,
            short_repo=short_repo,
            languages=[(code, LANGUAGE_NAMES[code]) for code in LANGUAGES],
            language=t.language,
            last_poll=self.poller.last_poll(),
            polling=self.poller.polling,
            poll_error=self.poller.last_error,
            repo_count=len(self.poller.repos()),
            config_path=str(config_path()),
            snooze_days=SNOOZE_DAYS,
            zoom=f"{self.zoom():.1f}",
            data_version=self.data_version(),
            self_url=request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        )
        return templates.TemplateResponse(request, name, context)


def create_app(poller: Poller) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_web / "static")), name="static")
    app.middleware("http")(local_only)
    web = Web(poller)
    add_pages(app, web)
    add_item_actions(app, web)
    add_settings(app, web)
    return app


async def local_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    # Refuse DNS rebinding (a foreign Host) and requests another site makes the browser send.
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    foreign = request.headers.get("sec-fetch-site") in FOREIGN_SITES or (
        origin and urlsplit(origin).hostname not in LOCAL_HOSTS
    )
    if host not in LOCAL_HOSTS or foreign:
        return Response("Forbidden", status_code=403)
    return await call_next(request)


def add_pages(app: FastAPI, web: Web) -> None:
    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request, org: str | None = None) -> HTMLResponse:
        sections = {s: by_org(es, org, lambda e: e.item.repo) for s, es in web.store.inbox(web.poller.clock()).items()}
        return web.render(
            request,
            "inbox.html",
            page="inbox",
            sections=[(section, sections[section]) for section in Section],
            total=sum(len(es) for es in sections.values()),
            baseline=web.store.baseline(),
            owners=web.owners(),
            org=org,
        )

    @app.get("/overview", response_class=HTMLResponse)
    def overview(
        request: Request,
        org: str | None = None,
        sort: str | None = None,
        sort_dir: Annotated[str | None, Query(alias="dir")] = None,
    ) -> HTMLResponse:
        if sort in SORT_COLUMNS and sort_dir in ("asc", "desc"):
            web.store.set_setting("overview_sort", f"{sort} {sort_dir}")
        column, direction = parse_sort(web.store.get_setting("overview_sort"))
        rows = by_org(web.store.overview(web.poller.repos(), web.poller.clock()), org, lambda row: row.repo)
        return web.render(
            request,
            "overview.html",
            page="overview",
            rows=sort_rows(rows, column, direction),
            owners=web.owners(),
            org=org,
            sort_column=column,
            sort_direction=direction,
            sort_link=lambda name: sort_link(name, column, direction, org),
        )

    @app.get("/repo/{owner}/{name}", response_class=HTMLResponse)
    def repo(request: Request, owner: str, name: str) -> HTMLResponse:
        full = f"{owner}/{name}"
        now = web.poller.clock()
        entries = {e.item.key: e for e in web.store.entries(now, full)}
        items = [i for i in web.store.items(full) if i.open or i.key in entries]
        groups = [(kind, sorted((i for i in items if i.kind is kind), key=lambda i: i.updated_at, reverse=True))
                  for kind in KIND_ORDER]  # fmt: skip
        row = next(iter(web.store.overview([full], now)), None)
        return web.render(request, "repo.html", page="overview", repo=full, row=row, groups=groups, entries=entries)

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request, seen: str | None = None) -> HTMLResponse:
        # `seen` is the data version the page shows. A newer one reloads the content in place.
        response = web.render(request, "status.html")
        if seen is not None and seen != web.data_version():
            response.headers.update(UPDATED)
        return response


def add_item_actions(app: FastAPI, web: Web) -> None:
    @app.post("/items/seen")
    async def seen(request: Request) -> Response:
        web.store.mark_seen([(await read_form(request))["key"]], web.poller.clock())
        return Response(headers=UPDATED)

    @app.post("/items/unseen")
    async def unseen(request: Request) -> Response:
        web.store.mark_unseen((await read_form(request))["key"])
        return Response(headers=UPDATED)

    @app.post("/items/snooze")
    async def snooze(request: Request) -> Response:
        data = await read_form(request)
        days = int(data.get("days", "0"))
        web.store.snooze(data["key"], web.poller.clock() + timedelta(days=days) if days > 0 else None)
        return Response(headers=UPDATED)

    @app.post("/items/open")
    async def open_item(request: Request) -> Response:
        # Opening an item on GitHub counts as seeing it. Its repository is polled again soon,
        # so a merge or a close there shows up here without waiting for the next full poll.
        key = (await read_form(request))["key"]
        web.store.mark_seen([key], web.poller.clock())
        web.poller.touch(key.split("#", 1)[0])
        return Response(status_code=204)

    @app.post("/focus")
    def focus() -> Response:
        web.poller.refresh_touched()
        return Response(status_code=204)

    @app.post("/seen-all")
    def seen_all() -> Response:
        web.store.mark_all_seen(web.poller.clock())
        return Response(headers=UPDATED)

    @app.post("/refresh")
    def refresh() -> Response:
        web.poller.refresh()
        web.poller.polling = True
        return Response(headers={"HX-Trigger": "repoboard-status"})


def add_settings(app: FastAPI, web: Web) -> None:
    @app.post("/zoom")
    async def set_zoom(request: Request) -> dict[str, float]:
        step = (await read_form(request)).get("step")
        current = web.zoom()
        changed = {"in": current + ZOOM_STEP, "out": current - ZOOM_STEP, "reset": 1.0}.get(step or "", current)
        value = round(clamp(changed, ZOOM_MIN, ZOOM_MAX), 1)
        web.store.set_setting("zoom", f"{value:.1f}")
        return {"zoom": value}

    @app.get("/window")
    def get_window() -> dict[str, int]:
        return web.window_size()

    @app.post("/window")
    def set_window(size: WindowSize) -> dict[str, int]:
        merged = web.window_size()
        for key, value in size.model_dump().items():
            if value is not None:
                merged[key] = int(clamp(value, *WINDOW_LIMITS[key]))
        web.store.set_setting("window", json.dumps(merged))
        return merged

    @app.post("/language")
    async def language(request: Request) -> RedirectResponse:
        data = await read_form(request)
        if data.get("language") in LANGUAGES:
            web.store.set_setting("language", data["language"])
        back = request.headers.get("referer") or "/"
        return RedirectResponse(urlsplit(back).path or "/", status_code=303)
