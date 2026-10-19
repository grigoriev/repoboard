"""Local web UI: the inbox, the overview and a page per repository. Served on 127.0.0.1 only."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from repoboard.config import LANGUAGES, config_path
from repoboard.i18n import LANGUAGE_NAMES, Translator
from repoboard.models import Item, Kind
from repoboard.poller import Poller
from repoboard.triage import Section

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
KIND_ORDER = (Kind.ALERT, Kind.PR, Kind.ISSUE, Kind.DISCUSSION)
SNOOZE_DAYS = (1, 7)

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
    return Markup('<span class="owner">{}/</span>{}').format(owner, name)


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
    def overview(request: Request, org: str | None = None) -> HTMLResponse:
        rows = store.overview(config.repos, poller.clock())
        if org:
            rows = [row for row in rows if row.repo.startswith(f"{org}/")]
        return render(request, "overview.html", page="overview", rows=rows, owners=owners(), org=org)

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
    def status(request: Request, seen: str = "") -> HTMLResponse:
        response = render(request, "status.html")
        last = poller.last_poll()
        if seen and last and last.isoformat() != seen:
            response.headers["HX-Refresh"] = "true"
        return response

    @app.post("/language")
    async def language(request: Request) -> RedirectResponse:
        data = await form(request)
        if data.get("language") in LANGUAGES:
            store.set_setting("language", data["language"])
        back = request.headers.get("referer") or "/"
        return RedirectResponse(urlsplit(back).path or "/", status_code=303)

    return app
