"""Fixtures for the visual tests: a seeded app server and a Chromium page."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

pytest.importorskip("playwright")

import uvicorn  # noqa: E402
from playwright.sync_api import Browser, Page, sync_playwright  # noqa: E402

from repoboard.config import Config  # noqa: E402
from repoboard.gh import Gh  # noqa: E402
from repoboard.poller import Poller  # noqa: E402
from repoboard.store import Store  # noqa: E402
from repoboard.web.app import create_app  # noqa: E402
from tests.fakes import FakeGhRunner  # noqa: E402
from tests.visual.data import NOW, REPOS, seed  # noqa: E402

Setup = Callable[[Poller], None]


STARTUP_SECONDS = 10


@contextmanager
def running(poller: Poller) -> Iterator[str]:
    # Bind once and hand the socket to uvicorn: probing a free port and binding later would race.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(poller), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_SECONDS
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the app server did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()


@pytest.fixture
def app() -> Iterator[Callable[..., str]]:
    """Start the app with the example data. Call it with options; it returns the base URL."""
    stack: list[tuple[Store, object]] = []

    def start(repos: tuple[str, ...] = REPOS, setup: Setup | None = None) -> str:
        store = Store(":memory:")
        poller = Poller(Config(repos=repos), store, Gh(FakeGhRunner({})), clock=lambda: NOW)
        if repos:
            seed(store)
        if setup:
            setup(poller)
        context = running(poller)
        url = context.__enter__()
        stack.append((store, context))
        return url

    yield start
    for store, context in reversed(stack):
        context.__exit__(None, None, None)  # type: ignore[attr-defined]
        store.close()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def page_for(browser: Browser) -> Iterator[Callable[..., Page]]:
    contexts = []

    def open_page(width: int = 1400, height: int = 900, scheme: str = "light") -> Page:
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            color_scheme=scheme,  # type: ignore[arg-type]
            reduced_motion="reduce",
            locale="en-US",
            timezone_id="UTC",
        )
        # Links lead to GitHub. The tests must not depend on the network, so those requests stop here.
        context.route("https://github.com/**", lambda route: route.abort())
        contexts.append(context)
        return context.new_page()

    yield open_page
    for context in contexts:
        context.close()
