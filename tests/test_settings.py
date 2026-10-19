from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from repoboard import config
from repoboard.config import Config, Owner
from repoboard.gh import Gh, GhError
from repoboard.poller import Poller
from repoboard.store import Store
from repoboard.web.app import Web, create_app
from repoboard.web.settings import apply, form_to_config
from tests.fakes import FakeGhRunner

NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    monkeypatch.setenv("REPOBOARD_CONFIG", str(path))
    return path


def repo(name: str, **flags: Any) -> dict[str, Any]:
    return {"full_name": name, "archived": False, "fork": False, "mirror_url": None, "is_template": False,
            "private": False, **flags}  # fmt: skip


@pytest.fixture
def poller(tmp_path: Path) -> Iterator[Poller]:
    store = Store(":memory:")
    runner = FakeGhRunner(
        {
            "api user": {"login": "me"},
            "orgs/acme/repos": [[repo("acme/tool"), repo("acme/old", archived=True), repo("acme/docs")]],
            "orgs/nobody/repos": GhError("Not Found", 404),
            "users/nobody/repos": GhError("Not Found", 404),
        }
    )
    cfg = Config(
        repos=("cli/cli",),
        owners=(Owner("acme", exclude=("docs",)),),
        items=("https://github.com/cli/cli/pull/1",),
        database=tmp_path / "db",
    )
    p = Poller(cfg, store, Gh(runner), clock=lambda: NOW)
    yield p
    store.close()


@pytest.fixture
def client(poller: Poller, config_file: Path) -> TestClient:
    return TestClient(create_app(poller), base_url="http://127.0.0.1")


def test_form_to_config_reads_every_field(tmp_path: Path) -> None:
    current = Config(port=9000, database=tmp_path / "db")
    form = {
        "owner_name": ["acme", "  ", "Beta"],
        "owner_include": ["", "", "ch.*, *polarion*"],
        "owner_exclude": ["docs old-*", "", ""],
        "owner_types_0": ["archived", "fork"],
        "owner_types_2": ["private"],
        "owner_remove_1": ["on"],
        "repos": ["cli/cli\n\n  o/r  \n"],
        "items": ["https://github.com/cli/cli/pull/1/files\n"],
        "language": ["de"],
        "poll_interval_minutes": ["5"],
        "lookback_days": ["7"],
        "hidden_alert_tools": ["Scorecard, SonarCloud"],
    }
    cfg = form_to_config(form, current)
    assert cfg.owners == (
        Owner("acme", exclude=("docs", "old-*"), exclude_types=("archived", "fork")),
        Owner("Beta", include=("ch.*", "*polarion*"), exclude_types=("private",)),
    )
    assert cfg.repos == ("cli/cli", "o/r")
    assert cfg.items == ("https://github.com/cli/cli/pull/1",)
    assert (cfg.language, cfg.poll_interval_minutes, cfg.lookback_days) == ("de", 5, 7)
    assert cfg.hidden_alert_tools == ("Scorecard", "SonarCloud")
    assert (cfg.port, cfg.database) == (9000, tmp_path / "db")


def test_form_to_config_drops_removed_owners() -> None:
    form = {"owner_name": ["acme"], "owner_remove_0": ["on"]}
    assert form_to_config(form, Config()).owners == ()


@pytest.mark.parametrize(
    "form",
    [
        {"poll_interval_minutes": ["often"]},
        {"repos": ["not a repo"]},
        {"items": ["https://example.com/x"]},
        {"owner_name": ["bad name"]},
    ],
)
def test_form_to_config_rejects(form: dict[str, list[str]]) -> None:
    empty = Config()
    with pytest.raises(config.ConfigError):
        form_to_config(form, empty)


def test_settings_page_shows_the_config(client: TestClient) -> None:
    page = client.get("/settings").text
    assert 'value="acme"' in page
    assert "docs" in page
    assert "cli/cli" in page
    assert "https://github.com/cli/cli/pull/1" in page
    assert 'name="owner_name" value=""' in page  # an empty row adds an owner


def test_saving_writes_the_config_and_applies_it(client: TestClient, poller: Poller, config_file: Path) -> None:
    form = {
        "owner_name": ["acme", "beta"],
        "owner_include": ["", ""],
        "owner_exclude": ["", ""],
        "owner_types_0": ["fork"],
        "repos": "cli/cli",
        "items": "",
        "language": "ru",
        "poll_interval_minutes": "15",
        "lookback_days": "14",
        "hidden_alert_tools": "Scorecard",
    }
    response = client.post("/settings", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"
    saved = config.load(config_file)
    assert [o.name for o in saved.owners] == ["acme", "beta"]
    assert saved.owners[0].exclude_types == ("fork",)
    assert saved.poll_interval_minutes == 15
    assert poller.config == saved
    assert "Настройки сохранены" in client.get("/settings?saved=1").text


def test_saving_invalid_input_keeps_the_config(client: TestClient, poller: Poller, config_file: Path) -> None:
    before = poller.config
    response = client.post("/settings", data={"owner_name": "acme", "repos": "nope"})
    assert response.status_code == 400
    assert "nope" in response.text
    assert poller.config == before
    assert not config_file.exists()


def test_preview_lists_the_matching_repositories(client: TestClient) -> None:
    page = client.get("/settings/preview", params={"owner": "acme", "exclude": "docs", "types": "archived"}).text
    assert "</span><wbr>tool<" in page
    assert ">docs<" not in page
    assert ">old<" not in page
    assert "1 of 3 repositories" in page
    assert "Not Found" in client.get("/settings/preview", params={"owner": "nobody"}).text


def test_watch_and_unwatch_buttons(client: TestClient, poller: Poller, config_file: Path) -> None:
    url = "https://github.com/o/r/issues/3"
    assert client.post("/items/watch", data={"url": url}).headers["HX-Trigger"] == "repoboard-updated"
    assert url in config.load(config_file).items
    assert url in poller.config.items
    client.post("/items/unwatch", data={"url": url})
    assert url not in poller.config.items
    assert client.post("/items/watch", data={"url": "nope"}).status_code == 400


def test_settings_page_is_left_out_of_the_live_reload(client: TestClient) -> None:
    assert "repoboard-updated" not in client.get("/settings").text.split("<main", 1)[1].split(">", 1)[0]
    assert 'hx-trigger="repoboard-updated from:body"' in client.get("/").text


def test_scanner_names_with_spaces_survive_a_save() -> None:
    cfg = form_to_config({"hidden_alert_tools": ["Semgrep OSS, Scorecard\nCodeQL"]}, Config())
    assert cfg.hidden_alert_tools == ("Semgrep OSS", "Scorecard", "CodeQL")


def test_the_language_field_wins_over_the_header_switcher(client: TestClient, poller: Poller) -> None:
    client.post("/language", data={"language": "de"})
    assert 'value="de" selected' in client.get("/settings").text
    client.post("/settings", data={"owner_name": "acme", "language": "ru"})
    assert "Обзор" in client.get("/").text


def test_errors_are_translated(client: TestClient) -> None:
    client.post("/language", data={"language": "de"})
    page = client.post("/settings", data={"poll_interval_minutes": "often"}).text
    assert "Nicht gespeichert" in page
    assert "ganze Zahlen" in page


def test_a_rejected_save_keeps_what_was_typed(client: TestClient) -> None:
    form = {
        "owner_name": ["acme", "beta"],
        "owner_include": ["", "x*"],
        "owner_exclude": ["", ""],
        "owner_types_1": ["private"],
        "repos": "cli/cli\nnot a repo",
        "items": "https://github.com/o/r/issues/1",
        "poll_interval_minutes": "15",
        "lookback_days": "14",
        "hidden_alert_tools": "Semgrep OSS",
    }
    page = client.post("/settings", data=form).text
    assert 'value="beta"' in page
    assert 'value="x*"' in page
    assert "not a repo" in page
    assert "https://github.com/o/r/issues/1" in page
    assert 'value="15"' in page
    assert 'value="Semgrep OSS"' in page


def test_a_rejected_save_keeps_the_chosen_language(client: TestClient) -> None:
    page = client.post("/settings", data={"repos": "not a repo", "language": "de"}).text
    # The header switcher keeps the language in use; the settings field shows the one picked.
    assert page.count('value="de" selected') == 1
    assert page.count('value="en" selected') == 1


def read_only(_: Config, path: Path | None = None) -> None:
    raise PermissionError(13, "Permission denied", "config.toml")


def test_a_save_that_cannot_write_keeps_the_input(
    client: TestClient, poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "save", read_only)
    before = poller.config
    response = client.post("/settings", data={"repos": "cli/cli\nnew/repo", "language": "en"})
    assert response.status_code == 400
    assert "Permission denied" in response.text
    assert "new/repo" in response.text
    assert poller.config == before


def test_a_watch_that_cannot_write_reports_it(
    client: TestClient, poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "save", read_only)
    before = poller.config
    response = client.post("/items/watch", data={"url": "https://github.com/cli/cli/issues/2"})
    assert response.status_code == 500
    assert "Permission denied" in response.text
    assert poller.config == before


def test_edits_at_the_same_time_keep_each_other(poller: Poller, config_file: Path) -> None:
    first_read = threading.Event()
    second_done = threading.Event()
    web = Web(poller)

    def slow_watch(current: Config) -> Config:
        first_read.set()
        second_done.wait(timeout=0.3)  # without the lock the second edit lands here and is lost
        return config.watch(current, "https://github.com/cli/cli/issues/2")

    def second_edit() -> None:
        first_read.wait(timeout=5)
        apply(web, lambda current: config.watch(current, "https://github.com/cli/cli/issues/3"))
        second_done.set()

    second = threading.Thread(target=second_edit, daemon=True)
    second.start()
    apply(web, slow_watch)
    second.join(timeout=5)
    assert {"https://github.com/cli/cli/issues/2", "https://github.com/cli/cli/issues/3"} <= set(poller.config.items)
