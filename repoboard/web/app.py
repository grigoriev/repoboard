"""Local web UI: the inbox, the overview and a page per repository. Served on 127.0.0.1 only."""

from __future__ import annotations

import json
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

from repoboard.config import LANGUAGES, config_path
from repoboard.i18n import LANGUAGE_NAMES, Translator
from repoboard.models import Item, Kind
from repoboard.poller import Poller
from repoboard.store import RepoRow
from repoboard.triage import Section

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
KIND_ORDER = (Kind.ALERT, Kind.PR, Kind.ISSUE, Kind.DISCUSSION)
SNOOZE_DAYS = (1, 7)
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.6, 2.0, 0.1
WINDOW_DEFAULT = {"width": 1200, "height": 820, "x": None, "y": None}
WINDOW_LIMITS = {"width": (400, 8000), "height": (300, 8000), "x": (-20000, 20000), "y": (-20000, 20000)}

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


class WindowGeometry(BaseModel):
    width: int | None = None
    height: int | None = None
    x: int | None = None
    y: int | None = None


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


def create_app(poller: Poller) -> FastAPI:
    store = poller.store
    config = poller.config
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(_web / "static")), name="static")

    @app.middleware("http")
    async def local_only(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # Refuse DNS rebinding (a foreign Host) and cross-site form posts (a foreign Origin).
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        origin = request.headers.get("origin")
        if host not in LOCAL_HOSTS or (origin and urlsplit(origin).hostname not in LOCAL_HOSTS):
            return Response("Forbidden", status_code=403)
        return await call_next(request)

    def translator() -> Translator:
        return Translator(store.get_setting("language") or config.language)

    def zoom() -> float:
        try:
            value = float(store.get_setting("zoom") or 1.0)
        except ValueError:
            value = 1.0
        return min(max(value, ZOOM_MIN), ZOOM_MAX)

    def render(request: Request, name: str, **context: Any) -> HTMLResponse:
        t = translator()
        now = poller.clock()
        context.update(
            t=t,
            now=now,
            age=lambda then: t.age(then, now),
            ref=item_ref,
            short_repo=short_repo,
            languages=[(code, LANGUAGE_NAMES[code]) for code in LANGUAGES],
            language=t.language,
            last_poll=poller.last_poll(),
            polling=poller.polling,
            poll_error=poller.last_error,
            repo_count=len(config.repos),
            config_path=str(config_path()),
            snooze_days=SNOOZE_DAYS,
            zoom=f"{zoom():.1f}",
        )
        return templates.TemplateResponse(request, name, context)

    async def form(request: Request) -> dict[str, str]:
        parsed = parse_qs((await request.body()).decode())
        return {key: values[0] for key, values in parsed.items()}

    def owners() -> list[str]:
        return sorted({repo.split("/", 1)[0] for repo in config.repos}, key=str.lower)

    @app.get("/", response_class=HTMLResponse)
    def inbox(request: Request, org: str | None = None) -> HTMLResponse:
        now = poller.clock()
        sections = store.inbox(now)
        if org:
            sections = {s: [e for e in es if e.item.repo.startswith(f"{org}/")] for s, es in sections.items()}
        return render(
            request,
            "inbox.html",
            page="inbox",
            sections=[(section, sections[section]) for section in Section],
            total=sum(len(es) for es in sections.values()),
            baseline=store.baseline(),
            owners=owners(),
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
            store.set_setting("overview_sort", f"{sort} {sort_dir}")
        column, direction = (store.get_setting("overview_sort") or "repo asc").split(" ", 1)
        rows = store.overview(config.repos, poller.clock())
        if org:
            rows = [row for row in rows if row.repo.startswith(f"{org}/")]

        def sort_link(name: str) -> str:
            first = SORT_COLUMNS[name][1]
            flipped = {"asc": "desc", "desc": "asc"}[direction] if name == column else first
            return f"/overview?{urlencode({'sort': name, 'dir': flipped, **({'org': org} if org else {})})}"

        return render(
            request,
            "overview.html",
            page="overview",
            rows=sort_rows(rows, column, direction),
            owners=owners(),
            org=org,
            sort_column=column,
            sort_direction=direction,
            sort_link=sort_link,
        )

    @app.get("/repo/{owner}/{name}", response_class=HTMLResponse)
    def repo(request: Request, owner: str, name: str) -> HTMLResponse:
        full = f"{owner}/{name}"
        now = poller.clock()
        entries = {e.item.key: e for e in store.entries(now, full)}
        items = [i for i in store.items(full) if i.open or i.key in entries]
        groups = [(kind, sorted((i for i in items if i.kind is kind), key=lambda i: i.updated_at, reverse=True))
                  for kind in KIND_ORDER]  # fmt: skip
        row = next((r for r in store.overview([full], now)), None)
        return render(request, "repo.html", page="overview", repo=full, row=row, groups=groups, entries=entries)

    @app.post("/items/seen")
    async def seen(request: Request) -> Response:
        store.mark_seen([(await form(request))["key"]], poller.clock())
        return Response()

    @app.post("/items/unseen")
    async def unseen(request: Request) -> Response:
        store.mark_unseen((await form(request))["key"])
        return Response()

    @app.post("/items/snooze")
    async def snooze(request: Request) -> Response:
        data = await form(request)
        days = int(data.get("days", "0"))
        store.snooze(data["key"], poller.clock() + timedelta(days=days) if days > 0 else None)
        return Response()

    @app.post("/seen-all")
    def seen_all() -> RedirectResponse:
        store.mark_all_seen(poller.clock())
        return RedirectResponse("/", status_code=303)

    @app.post("/refresh")
    def refresh() -> Response:
        poller.refresh()
        poller.polling = True
        return Response(headers={"HX-Trigger": "repoboard-status"})

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request, seen: str | None = None) -> HTMLResponse:
        # `seen` is the poll the page shows, "never" before the first one. A newer poll reloads the page.
        response = render(request, "status.html")
        last = poller.last_poll()
        if seen is not None and last and last.isoformat() != seen:
            response.headers["HX-Refresh"] = "true"
        return response

    @app.post("/zoom")
    async def set_zoom(request: Request) -> dict[str, float]:
        step = (await form(request)).get("step")
        current = zoom()
        changed = {"in": current + ZOOM_STEP, "out": current - ZOOM_STEP, "reset": 1.0}.get(step or "", current)
        value = round(min(max(changed, ZOOM_MIN), ZOOM_MAX), 1)
        store.set_setting("zoom", f"{value:.1f}")
        return {"zoom": value}

    def window_geometry() -> dict[str, int | None]:
        try:
            saved = json.loads(store.get_setting("window") or "{}")
        except ValueError:
            saved = {}
        return {key: saved.get(key, default) for key, default in WINDOW_DEFAULT.items()}

    @app.get("/window")
    def get_window() -> dict[str, int | None]:
        return window_geometry()

    @app.post("/window")
    def set_window(geometry: WindowGeometry) -> dict[str, int | None]:
        merged = window_geometry()
        for key, value in geometry.model_dump().items():
            if value is not None:
                low, high = WINDOW_LIMITS[key]
                merged[key] = min(max(value, low), high)
        store.set_setting("window", json.dumps(merged))
        return merged

    @app.post("/language")
    async def language(request: Request) -> RedirectResponse:
        data = await form(request)
        if data.get("language") in LANGUAGES:
            store.set_setting("language", data["language"])
        back = request.headers.get("referer") or "/"
        return RedirectResponse(urlsplit(back).path or "/", status_code=303)

    return app
