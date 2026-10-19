from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from repoboard.config import Config
from repoboard.gh import Gh
from repoboard.models import Access, AlertSource, Item, Kind, RepoSnapshot, Tag
from repoboard.poller import Poller
from repoboard.store import Store
from repoboard.web.app import create_app, item_ref, short_repo
from tests.fakes import FakeGhRunner

NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)
HOUR = timedelta(hours=1)


def item(number: str, kind: Kind = Kind.ISSUE, repo: str = "o/r", **changes: object) -> Item:
    at = NOW - HOUR
    base = Item(
        repo=repo,
        kind=kind,
        number=number,
        title=f"Title {number}",
        url=f"https://github.com/{repo}/issues/{number}",
        author="alice",
        open=True,
        created_at=at,
        updated_at=at,
        activity_at=at,
        activity_by="alice",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.fixture
def poller() -> Iterator[Poller]:
    store = Store(":memory:")
    p = Poller(Config(repos=("o/r", "x/y")), store, Gh(FakeGhRunner({})), clock=lambda: NOW)
    yield p
    store.close()


@pytest.fixture
def client(poller: Poller) -> TestClient:
    return TestClient(create_app(poller), base_url="http://127.0.0.1:8765")


def seed(poller: Poller) -> None:
    poller.store.ensure_baseline(NOW)
    items = (
        item("1"),
        item("2", created_at=NOW - timedelta(days=30), activity_by="bob"),
        item("3", Kind.PR, tags=(Tag.CHECKS_FAILURE,), activity_at=None),
        item("17", Kind.DISCUSSION),
        item("dependabot/4", Kind.ALERT, tags=(Tag.HIGH,)),
        item("9", open=False, activity_at=None),
    )
    security = {AlertSource.DEPENDABOT: Access.OK, AlertSource.SECRET_SCANNING: Access.NO_ACCESS}
    poller.store.save_snapshots(
        [RepoSnapshot("o/r", items, open_discussions=1, security=security), RepoSnapshot("x/y", error="gone")], NOW
    )
    poller.store.set_setting("last_poll", NOW.isoformat())


def test_item_ref() -> None:
    assert item_ref(item("5")) == "#5"
    assert item_ref(item("17", Kind.DISCUSSION)) == "D17"
    assert item_ref(item("code_scanning/2", Kind.ALERT, author="CodeQL")) == "CodeQL"
    assert str(short_repo("o/<r>")) == '<span class="owner">o/</span>&lt;r&gt;'


def test_inbox_empty_and_no_repos(client: TestClient, poller: Poller) -> None:
    page = client.get("/").text
    assert "Nothing new" in page
    assert "Not polled yet" in page
    poller.config = Config()
    assert "No repositories yet" in TestClient(create_app(poller), base_url="http://127.0.0.1").get("/").text


def test_inbox_lists_sections(client: TestClient, poller: Poller) -> None:
    seed(poller)
    page = client.get("/").text
    for text in (
        "Security",
        "Failed PRs",
        "New replies",
        "reply by bob",
        "D17",
        "checks failed",
        "Last poll now",
        "<th",
    ):
        assert text in page
    assert "Title 9" not in page
    assert "Title 1" not in client.get("/?org=x").text


def test_seen_unseen_and_snooze(client: TestClient, poller: Poller) -> None:
    seed(poller)
    assert client.post("/items/seen", data={"key": "o/r#issue#1"}).status_code == 200
    assert "Title 1<" not in client.get("/").text
    client.post("/items/unseen", data={"key": "o/r#issue#1"})
    assert "Title 1<" in client.get("/").text
    client.post("/items/snooze", data={"key": "o/r#issue#1", "days": "7"})
    assert "Title 1<" not in client.get("/").text
    assert "Unsnooze" in client.get("/repo/o/r").text
    client.post("/items/snooze", data={"key": "o/r#issue#1", "days": "0"})
    assert "Title 1<" in client.get("/").text


def test_seen_all_redirects(client: TestClient, poller: Poller) -> None:
    seed(poller)
    response = client.post("/seen-all", follow_redirects=False)
    assert response.status_code == 303
    assert "Nothing new" in client.get("/").text


def test_overview(client: TestClient, poller: Poller) -> None:
    seed(poller)
    page = client.get("/overview").text
    assert 'href="/repo/o/r"' in page and 'title="o/r"' in page
    assert 'title="gone"' in page
    assert "x/y" not in client.get("/overview?org=o").text


def test_repo_page(client: TestClient, poller: Poller) -> None:
    seed(poller)
    page = client.get("/repo/o/r").text
    assert "Title 3" in page and "Title 17" in page
    assert "Secret scanning: no access to security alerts" in page
    assert "Title 9" not in page
    assert "Could not read the repository: gone" in client.get("/repo/x/y").text
    assert "No open items." in client.get("/repo/x/y").text


def test_refresh_and_status(client: TestClient, poller: Poller) -> None:
    response = client.post("/refresh")
    assert response.headers["HX-Trigger"] == "repoboard-status"
    assert "Refreshing" in client.get("/status").text
    poller.polling = False
    poller.last_error = "auth required"
    poller.store.set_setting("last_poll", NOW.isoformat())
    status = client.get("/status", params={"seen": (NOW - HOUR).isoformat()})
    assert status.headers.get("HX-Refresh") == "true"
    assert "auth required" in status.text
    assert "HX-Refresh" not in client.get("/status", params={"seen": NOW.isoformat()}).headers


def test_language_switch(client: TestClient) -> None:
    response = client.post(
        "/language",
        data={"language": "ru"},
        headers={"referer": "http://127.0.0.1/overview?x=1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/overview"
    assert "Обзор" in client.get("/").text
    client.post("/language", data={"language": "fr"})
    assert "Обзор" in client.get("/").text
    client.post("/language", data={"language": "de"})
    assert "Übersicht" in client.get("/").text


def test_static_files(client: TestClient) -> None:
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


@pytest.mark.parametrize(
    ("base", "headers"),
    [("http://evil.example", {}), ("http://127.0.0.1", {"origin": "http://evil.example"})],
)
def test_rejects_foreign_host_and_origin(poller: Poller, base: str, headers: dict[str, str]) -> None:
    client = TestClient(create_app(poller), base_url=base)
    assert client.post("/seen-all", headers=headers).status_code == 403


def test_accepts_local_origin(client: TestClient) -> None:
    assert client.post("/seen-all", headers={"origin": "http://localhost:8765"}).status_code == 200


def test_status_link_survives_url_decoding(client: TestClient, poller: Poller) -> None:
    poller.store.set_setting("last_poll", NOW.isoformat())
    page = client.get("/status").text
    link = page.split('hx-get="', 1)[1].split('"', 1)[0]
    assert "HX-Refresh" not in client.get(link.replace("&amp;", "&")).headers


def test_page_reloads_after_the_first_poll(client: TestClient, poller: Poller) -> None:
    link = client.get("/status").text.split('hx-get="', 1)[1].split('"', 1)[0]
    assert "HX-Refresh" not in client.get(link).headers
    poller.store.set_setting("last_poll", NOW.isoformat())
    assert client.get(link).headers.get("HX-Refresh") == "true"


def test_language_label_and_local_baseline(client: TestClient, poller: Poller) -> None:
    seed(poller)
    client.post("/language", data={"language": "de"})
    page = client.get("/").text
    assert 'aria-label="Sprache"' in page
    local = (NOW - timedelta(days=7)).astimezone().strftime("%Y-%m-%d %H:%M")
    assert f"Neu seit {local}" in page
