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
    assert str(short_repo("o/<r>")) == '<span class="owner">o/</span><wbr>&lt;r&gt;'


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
    assert 'href="/repo/o/r"' in page
    assert 'title="o/r"' in page
    assert 'title="gone"' in page
    assert "x/y" not in client.get("/overview?org=o").text


def test_repo_page(client: TestClient, poller: Poller) -> None:
    seed(poller)
    page = client.get("/repo/o/r").text
    assert "Title 3" in page
    assert "Title 17" in page
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


def test_text_size_defaults_to_one(client: TestClient) -> None:
    page = client.get("/").text
    assert "--zoom: 1.0" in page
    assert '>A+<span class="sr-only"> Larger text</span>' in page


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (["in"], "1.1"),
        (["in", "in", "out"], "1.1"),
        (["out"], "0.9"),
        (["in", "reset"], "1.0"),
        (["in"] * 20, "2.0"),
        (["out"] * 20, "0.6"),
    ],
)
def test_text_size_steps_and_limits(client: TestClient, poller: Poller, steps: list[str], expected: str) -> None:
    for step in steps:
        response = client.post("/zoom", data={"step": step})
        assert response.status_code == 200
    assert response.json() == {"zoom": float(expected)}
    assert poller.store.get_setting("zoom") == expected
    assert f"--zoom: {expected}" in client.get("/overview").text


def test_text_size_ignores_unknown_step(client: TestClient) -> None:
    assert client.post("/zoom", data={"step": "huge"}).json() == {"zoom": 1.0}


def test_text_size_survives_a_broken_setting(client: TestClient, poller: Poller) -> None:
    poller.store.set_setting("zoom", "big")
    assert "--zoom: 1.0" in client.get("/").text


def seed_overview(poller: Poller) -> None:
    def issues(repo: str, count: int, hours: int) -> tuple[Item, ...]:
        at = NOW - timedelta(hours=hours)
        return tuple(item(str(n), repo=repo, created_at=at, updated_at=at, activity_at=None) for n in range(count))

    poller.config = Config(repos=("a/bbb", "b/aaa", "c/ccc"))
    poller.store.save_snapshots(
        [
            RepoSnapshot("a/bbb", issues("a/bbb", 2, 5)),
            RepoSnapshot("b/aaa", issues("b/aaa", 5, 1)),
            RepoSnapshot("c/ccc", ()),
        ],
        NOW,
    )


def overview_order(client: TestClient, query: str = "") -> list[str]:
    page = client.get(f"/overview{query}").text
    return [
        part.split('"', 1)[0] for part in page.split('<td class="repo')[1:] for part in [part.split('title="', 1)[1]]
    ]


def test_overview_sorts_by_column(poller: Poller) -> None:
    seed_overview(poller)
    client = TestClient(create_app(poller), base_url="http://127.0.0.1")
    assert overview_order(client) == ["a/bbb", "b/aaa", "c/ccc"]
    assert overview_order(client, "?sort=issues&dir=desc") == ["b/aaa", "a/bbb", "c/ccc"]
    assert overview_order(client, "?sort=issues&dir=asc") == ["c/ccc", "a/bbb", "b/aaa"]
    assert overview_order(client, "?sort=activity&dir=desc") == ["b/aaa", "a/bbb", "c/ccc"]
    assert overview_order(client, "?sort=activity&dir=asc") == ["a/bbb", "b/aaa", "c/ccc"]
    assert overview_order(client, "?sort=repo&dir=desc") == ["c/ccc", "b/aaa", "a/bbb"]


def test_overview_remembers_the_sort(poller: Poller) -> None:
    seed_overview(poller)
    client = TestClient(create_app(poller), base_url="http://127.0.0.1")
    client.get("/overview?sort=issues&dir=desc")
    assert overview_order(client) == ["b/aaa", "a/bbb", "c/ccc"]
    assert overview_order(client, "?sort=bogus&dir=sideways") == ["b/aaa", "a/bbb", "c/ccc"]


def test_overview_header_links_toggle_direction(poller: Poller) -> None:
    seed_overview(poller)
    client = TestClient(create_app(poller), base_url="http://127.0.0.1")
    page = client.get("/overview?sort=issues&dir=desc&org=a").text
    assert 'href="/overview?sort=issues&amp;dir=asc&amp;org=a"' in page
    assert 'href="/overview?sort=prs&amp;dir=desc&amp;org=a"' in page
    assert 'href="/overview?sort=repo&amp;dir=asc&amp;org=a"' in page
    assert 'aria-sort="descending"' in page


def test_window_size(client: TestClient, poller: Poller) -> None:
    assert client.get("/window").json() == {"width": 1200, "height": 820}
    assert client.post("/window", json={"width": 1600, "height": 1000}).status_code == 200
    assert client.get("/window").json() == {"width": 1600, "height": 1000}
    client.post("/window", json={"width": 50})
    assert client.get("/window").json() == {"width": 400, "height": 1000}
    client.post("/window", json={"height": 99999})
    assert client.get("/window").json() == {"width": 400, "height": 8000}
    assert client.post("/window", json={"width": "wide"}).status_code == 422


@pytest.mark.parametrize("saved", ["not json", "null", "[]", '{"width": "wide"}'])
def test_window_size_survives_a_broken_setting(client: TestClient, poller: Poller, saved: str) -> None:
    poller.store.set_setting("window", saved)
    assert client.get("/window").json() == {"width": 1200, "height": 820}


@pytest.mark.parametrize("saved", ["nan", "inf", "-inf"])
def test_text_size_survives_a_non_finite_setting(client: TestClient, poller: Poller, saved: str) -> None:
    poller.store.set_setting("zoom", saved)
    assert "--zoom: 1.0" in client.get("/").text
    assert client.post("/zoom", data={"step": "in"}).json() == {"zoom": 1.1}


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_rejects_requests_from_other_sites(client: TestClient, poller: Poller, site: str) -> None:
    response = client.get("/overview?sort=alerts&dir=asc", headers={"sec-fetch-site": site})
    assert response.status_code == 403
    assert poller.store.get_setting("overview_sort") is None


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_accepts_requests_from_the_app_itself(client: TestClient, site: str) -> None:
    assert client.get("/", headers={"sec-fetch-site": site}).status_code == 200


def test_sort_ties_stay_alphabetical() -> None:
    from repoboard.store import RepoRow
    from repoboard.web.app import sort_rows

    rows = [RepoRow(repo, None, None, failed=1) for repo in ("o/c", "o/a", "o/b")]
    assert [r.repo for r in sort_rows(rows, "failed", "desc")] == ["o/a", "o/b", "o/c"]


@pytest.mark.parametrize("saved", ["garbage", "stars desc", "repo sideways"])
def test_overview_survives_a_broken_saved_sort(poller: Poller, saved: str) -> None:
    seed_overview(poller)
    poller.store.set_setting("overview_sort", saved)
    client = TestClient(create_app(poller), base_url="http://127.0.0.1")
    assert overview_order(client) == ["a/bbb", "b/aaa", "c/ccc"]
