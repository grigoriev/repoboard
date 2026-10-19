"""Configuration: the repositories to watch and the app settings, stored as TOML."""

from __future__ import annotations

import copy
import json
import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

LANGUAGES = ("en", "ru", "de")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "repoboard" / "config.toml"
DEFAULT_DATABASE_PATH = Path.home() / ".local" / "state" / "repoboard" / "repoboard.sqlite3"
ALFRED_VISIBLE_DIR = (
    (Path.home() / "Library" / "Application Support" / "Alfred" / "Workflow Data")
    / "com.grigoriev.alfred.github"
    / "visible"
)


class ConfigError(ValueError):
    """The config file is invalid."""


@dataclass(frozen=True)
class Config:
    repos: tuple[str, ...] = ()
    language: str = "en"
    poll_interval_minutes: int = 10
    lookback_days: int = 14
    port: int = 8765
    database: Path = field(default=DEFAULT_DATABASE_PATH)
    # Code scanning tools whose alerts stay out of Repoboard. Scorecard reports repository hygiene, not
    # vulnerabilities, and fills the security section of every repository with the same few findings.
    hidden_alert_tools: tuple[str, ...] = ("Scorecard",)


def config_path() -> Path:
    override = os.environ.get("REPOBOARD_CONFIG")
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse(data)


def parse(data: dict[str, object]) -> Config:
    defaults = Config()
    repos = data.get("repos", [])
    if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
        raise ConfigError("repos must be a list of 'owner/name' strings")
    bad = [r for r in repos if not REPO_PATTERN.match(r)]
    if bad:
        raise ConfigError(f"invalid repo names: {', '.join(bad)}")
    language = data.get("language", defaults.language)
    if language not in LANGUAGES:
        raise ConfigError(f"language must be one of {', '.join(LANGUAGES)}")
    database = data.get("database")
    hidden = data.get("hidden_alert_tools", list(defaults.hidden_alert_tools))
    if not isinstance(hidden, list) or not all(isinstance(tool, str) for tool in hidden):
        raise ConfigError("hidden_alert_tools must be a list of tool names")
    return Config(
        repos=tuple(dict.fromkeys(repos)),
        language=str(language),
        poll_interval_minutes=_positive_int(data, "poll_interval_minutes", defaults.poll_interval_minutes),
        lookback_days=_positive_int(data, "lookback_days", defaults.lookback_days),
        port=_port(data, defaults.port),
        database=Path(database).expanduser() if isinstance(database, str) else defaults.database,
        hidden_alert_tools=tuple(hidden),
    )


def _positive_int(data: dict[str, object], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer")
    return value


def _port(data: dict[str, object], default: int) -> int:
    port = _positive_int(data, "port", default)
    if port > 65535:
        raise ConfigError("port must be at most 65535")
    return port


def dump(config: Config) -> str:
    repos = "".join(f"    {json.dumps(r)},\n" for r in config.repos)
    return (
        f'language = "{config.language}"\n'
        f"poll_interval_minutes = {config.poll_interval_minutes}\n"
        f"lookback_days = {config.lookback_days}\n"
        f"port = {config.port}\n"
        f"database = {json.dumps(str(config.database))}\n"
        f"hidden_alert_tools = {json.dumps(list(config.hidden_alert_tools))}\n"
        f"repos = [\n{repos}]\n"
    )


def save(config: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(config), encoding="utf-8")


def read_alfred_repos(directory: Path = ALFRED_VISIBLE_DIR) -> list[str]:
    """Read the visible repos of the Alfred GitHub workflow: one 'owner/name' per line, '#' hides a line."""
    repos: list[str] = []
    for file in sorted(directory.iterdir()) if directory.is_dir() else []:
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and REPO_PATTERN.match(line):
                repos.append(line)
    return repos


def with_repos(config: Config, repos: Iterable[str]) -> Config:
    return copy.replace(config, repos=tuple(dict.fromkeys([*config.repos, *repos])))
