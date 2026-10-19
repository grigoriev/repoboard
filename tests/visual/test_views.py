"""Screenshots of every view, compared with the baselines in tests/visual/snapshots."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page

from repoboard.poller import Poller
from tests.visual.data import REPOS
from tests.visual.snapshot import assert_matches

pytestmark = pytest.mark.visual


def mark_all_seen(poller: Poller) -> None:
    poller.store.mark_all_seen(poller.clock())


def language(code: str) -> Callable[[Poller], None]:
    return lambda poller: poller.store.set_setting("language", code)


def zoom(value: str) -> Callable[[Poller], None]:
    return lambda poller: poller.store.set_setting("zoom", value)


def sort(value: str) -> Callable[[Poller], None]:
    return lambda poller: poller.store.set_setting("overview_sort", value)


def broken_repo(poller: Poller) -> None:
    from repoboard.models import RepoSnapshot

    poller.store.save_snapshots(
        [RepoSnapshot(REPOS[2], error="GraphQL: Could not resolve to a Repository")], poller.clock()
    )


CASES = [
    # name, path, options for the app, options for the page
    ("inbox", "/", {}, {}),
    ("inbox-dark", "/", {}, {"scheme": "dark"}),
    ("inbox-org-filter", "/?org=grigoriev", {}, {}),
    ("inbox-empty", "/", {"setup": mark_all_seen}, {}),
    ("inbox-no-repos", "/", {"repos": ()}, {}),
    ("inbox-ru", "/", {"setup": language("ru")}, {}),
    ("inbox-de", "/", {"setup": language("de")}, {}),
    ("inbox-zoom-130", "/", {"setup": zoom("1.3")}, {}),
    ("inbox-narrow", "/", {}, {"width": 800}),
    ("overview", "/overview", {}, {}),
    ("overview-dark", "/overview", {}, {"scheme": "dark"}),
    ("overview-sorted-by-alerts", "/overview", {"setup": sort("alerts desc")}, {}),
    ("overview-repo-error", "/overview", {"setup": broken_repo}, {}),
    ("repo", "/repo/SchweizerischeBundesbahnen/ch.sbb.polarion.extension.pdf-exporter", {}, {}),
    ("repo-no-security-access", "/repo/intechcore/nginx-geoip2", {}, {}),
    ("repo-error", "/repo/intechcore/nginx-geoip2", {"setup": broken_repo}, {}),
]


@pytest.mark.parametrize(("name", "path", "app_options", "page_options"), CASES, ids=[case[0] for case in CASES])
def test_view(
    app: Callable[..., str],
    page_for: Callable[..., Page],
    name: str,
    path: str,
    app_options: dict[str, object],
    page_options: dict[str, object],
) -> None:
    url = app(**app_options)
    page = page_for(**page_options)
    page.goto(url + path)
    page.wait_for_load_state("networkidle")
    assert_matches(name, page.screenshot(full_page=True, animations="disabled", caret="hide"))
