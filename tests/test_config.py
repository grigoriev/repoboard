from __future__ import annotations

from pathlib import Path

import pytest

from repoboard import config
from repoboard.config import Config, ConfigError


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert config.load(tmp_path / "none.toml") == Config()
    assert Config().hidden_alert_tools == ("Scorecard",)


def test_hidden_alert_tools(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('hidden_alert_tools = ["Scorecard", "SonarCloud"]\n')
    assert config.load(path).hidden_alert_tools == ("Scorecard", "SonarCloud")
    path.write_text("hidden_alert_tools = []\n")
    assert config.load(path).hidden_alert_tools == ()


def test_load_full(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        'language = "de"\npoll_interval_minutes = 5\nlookback_days = 3\nport = 9000\n'
        'database = "~/x.db"\nrepos = ["a/b", "c/d", "a/b"]\n'
    )
    loaded = config.load(path)
    assert loaded.repos == ("a/b", "c/d")
    assert loaded.language == "de"
    assert loaded.poll_interval_minutes == 5
    assert loaded.lookback_days == 3
    assert loaded.port == 9000
    assert loaded.database == Path("~/x.db").expanduser()


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOBOARD_CONFIG", str(tmp_path / "c.toml"))
    assert config.config_path() == tmp_path / "c.toml"
    monkeypatch.delenv("REPOBOARD_CONFIG")
    assert config.config_path() == config.DEFAULT_CONFIG_PATH


@pytest.mark.parametrize(
    "data",
    [
        {"repos": "a/b"},
        {"repos": [1]},
        {"repos": ["no-slash"]},
        {"language": "fr"},
        {"port": 0},
        {"port": 70000},
        {"port": "80"},
        {"poll_interval_minutes": True},
        {"hidden_alert_tools": "Scorecard"},
        {"hidden_alert_tools": [1]},
    ],
)
def test_invalid(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        config.parse(data)


def test_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("repos = [")
    with pytest.raises(ConfigError):
        config.load(path)


def test_save_round_trip(tmp_path: Path) -> None:
    original = Config(
        repos=("a/b", "c/d"),
        language="ru",
        port=1234,
        database=tmp_path / 'we"ird\\db',
        hidden_alert_tools=("Scorecard", 'Sonar "Cloud"'),
    )
    path = tmp_path / "sub" / "c.toml"
    config.save(original, path)
    assert config.load(path) == original


def test_read_alfred_repos(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("# comment\na/one\n#a/hidden\n\n bad line\na/two\n")
    (tmp_path / "b").write_text("b/three\n")
    assert config.read_alfred_repos(tmp_path) == ["a/one", "a/two", "b/three"]
    assert config.read_alfred_repos(tmp_path / "missing") == []


def test_with_repos_keeps_order_and_dedupes() -> None:
    assert config.with_repos(Config(repos=("a/b",)), ["c/d", "a/b"]).repos == ("a/b", "c/d")


def test_with_repos_keeps_every_other_field(tmp_path: Path) -> None:
    original = Config(
        repos=("a/b",), language="de", poll_interval_minutes=3, lookback_days=2, port=9, database=tmp_path
    )
    updated = config.with_repos(original, ["c/d"])
    for name in Config.__dataclass_fields__:
        if name != "repos":
            assert getattr(updated, name) == getattr(original, name), name


def test_owners(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(
        '[[owners]]\nname = "grigoriev"\n\n'
        '[[owners]]\nname = "intechcore"\nexclude = ["articles"]\n\n'
        '[[owners]]\nname = "SchweizerischeBundesbahnen"\ninclude = ["ch.sbb.polarion.*"]\n'
        'exclude_types = ["archived", "fork", "private"]\n'
    )
    owners = config.load(path).owners
    assert owners[0] == config.Owner("grigoriev")
    assert owners[0].exclude_types == ("archived", "fork")
    assert owners[1].exclude == ("articles",)
    assert owners[2].include == ("ch.sbb.polarion.*",)
    assert owners[2].exclude_types == ("archived", "fork", "private")


@pytest.mark.parametrize(
    "owners",
    [
        "x",
        [{"exclude": []}],
        [{"name": "bad name"}],
        [{"name": "o", "include": "x"}],
        [{"name": "o", "exclude_types": ["sideways"]}],
        [{"name": "o"}, {"name": "O"}],
    ],
)
def test_invalid_owners(owners: object) -> None:
    with pytest.raises(ConfigError):
        config.parse({"owners": owners})


def test_owners_round_trip(tmp_path: Path) -> None:
    original = Config(
        repos=("cli/cli",),
        owners=(
            config.Owner("grigoriev"),
            config.Owner("intechcore", include=("a*",), exclude=('we"ird',), exclude_types=("private",)),
        ),
    )
    path = tmp_path / "c.toml"
    config.save(original, path)
    assert config.load(path) == original


@pytest.mark.parametrize(
    ("url", "repo", "kind", "number", "canonical"),
    [
        ("https://github.com/cli/cli/pull/123", "cli/cli", "pr", "123", "https://github.com/cli/cli/pull/123"),
        ("https://github.com/cli/cli/issues/7", "cli/cli", "issue", "7", "https://github.com/cli/cli/issues/7"),
        ("https://github.com/o/r/discussions/17#x", "o/r", "discussion", "17", "https://github.com/o/r/discussions/17"),
        ("github.com/o/r/pull/5/files", "o/r", "pr", "5", "https://github.com/o/r/pull/5"),
    ],
)
def test_parse_item_url(url: str, repo: str, kind: str, number: str, canonical: str) -> None:
    parsed = config.parse_item_url(url)
    assert (parsed.repo, parsed.kind, parsed.number, parsed.url) == (repo, kind, number, canonical)


@pytest.mark.parametrize(
    "url", ["https://gitlab.com/o/r/issues/1", "https://github.com/o/r", "https://github.com/o/r/pull/x"]
)
def test_parse_item_url_rejects(url: str) -> None:
    with pytest.raises(ConfigError):
        config.parse_item_url(url)


def test_items_in_the_config(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('items = ["https://github.com/cli/cli/pull/123", "https://github.com/cli/cli/pull/123/files"]\n')
    loaded = config.load(path)
    assert loaded.items == ("https://github.com/cli/cli/pull/123",)
    config.save(loaded, path)
    assert config.load(path) == loaded
    with pytest.raises(ConfigError):
        config.parse({"items": ["nope"]})


def test_watch_and_unwatch() -> None:
    cfg = config.watch(Config(), "https://github.com/o/r/issues/3")
    assert cfg.items == ("https://github.com/o/r/issues/3",)
    assert config.watch(cfg, "https://github.com/o/r/issues/3").items == cfg.items
    assert config.unwatch(cfg, "github.com/o/r/issues/3#issuecomment-9").items == ()
