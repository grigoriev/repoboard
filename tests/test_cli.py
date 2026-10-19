from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from repoboard import __version__, config
from repoboard.cli import main, port_free
from repoboard.gh import Gh, GhError
from tests.fakes import FakeGhRunner, empty_repo_routes


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    monkeypatch.setenv("REPOBOARD_CONFIG", str(path))
    return path


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_off_macos(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert main([]) == 0
    assert "serve" in capsys.readouterr().out


@pytest.mark.parametrize(("argv", "platform"), [([], "darwin"), (["tray"], "linux")])
def test_tray_is_the_default_on_macos(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], platform: str
) -> None:
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setattr("repoboard.cli.tray", lambda cfg: 7)
    assert main(argv) == 7


def test_port_free() -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        assert not port_free(busy.getsockname()[1])
    assert port_free(0)


def test_invalid_config(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_file.write_text('language = "fr"\n')
    assert main(["poll"]) == 2
    assert "language" in capsys.readouterr().err


def test_import_alfred(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(config, "read_alfred_repos", lambda: ["o/a", "o/b"])
    config.save(config.Config(repos=("o/a",), database=tmp_path / "db"), config_file)
    assert main(["import-alfred"]) == 0
    assert config.load(config_file).repos == ("o/a", "o/b")
    assert "1 added, 2 in" in capsys.readouterr().out


def _fake_gh(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> None:
    runner = FakeGhRunner(routes)
    monkeypatch.setattr("repoboard.cli.Gh", lambda: Gh(runner))


def test_poll_prints_counts(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config.save(config.Config(repos=("o/r",), database=tmp_path / "db.sqlite3"), config_file)
    _fake_gh(monkeypatch, {"api user": {"login": "me"}, **empty_repo_routes()})
    assert main(["poll"]) == 0
    out = capsys.readouterr().out
    assert "security   0" in out and "replies    0" in out


def test_poll_reports_error(
    config_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config.save(config.Config(repos=("o/r",), database=tmp_path / "db.sqlite3"), config_file)
    _fake_gh(monkeypatch, {"api user": GhError("not logged in")})
    assert main(["poll"]) == 1
    assert "not logged in" in capsys.readouterr().err
