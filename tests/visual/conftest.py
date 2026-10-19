"""Fixtures for the visual tests: a seeded app server and a Chromium page."""

from __future__ import annotations

import socket
import threading
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


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def running(poller: Poller) -> Iterator[str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(poller), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        threading.Event().wait(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


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
        contexts.append(context)
        return context.new_page()

    yield open_page
    for context in contexts:
        context.close()
