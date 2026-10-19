# CLAUDE.md

This file guides Claude Code when it works on or reviews this repository.

## Project Overview

Repoboard is a desktop app that gives a daily overview of many GitHub repositories. It polls them
through the `gh` CLI, sorts what it finds into inbox sections, remembers what the user has seen and
sends desktop notifications. It runs in the macOS menu bar and serves its UI on `127.0.0.1`.

## Tech Stack

- **Python**: 3.14+, package manager uv, build backend hatchling
- **GitHub access**: the `gh` CLI only. The app never holds a token
- **Storage**: SQLite through the standard library
- **UI**: FastAPI + uvicorn, Jinja2 templates, vendored htmx 2.0.4 in `repoboard/web/static`
- **Desktop**: rumps (menu bar), pywebview (window), desktop-notifier with an `osascript` fallback
- **Lint and format**: ruff (line length 120). **Type check**: mypy strict
- **Tests**: pytest with warnings as errors, pytest-cov (branch coverage, `fail_under = 90`)

## Common Commands

```sh
uv sync --all-extras                           # install the dependencies
uv run ruff check .                            # lint
uv run ruff format --check .                   # format check
uv run mypy repoboard tests                    # type check
uv run pytest --cov --cov-report=term-missing  # tests with coverage
uv run repoboard serve                         # run the web UI with the background poller
```

Point `REPOBOARD_CONFIG` at a scratch config to try changes without touching your own.

## Architecture

Package `repoboard/`, console entry point `repoboard` (`repoboard.cli:main`):

- `config.py`: TOML config, validation, Alfred list import
- `gh.py`: the only place that runs `gh`. `Gh` takes a runner, so tests inject fakes
- `collector.py`: issues, pull requests, discussions and alerts of one repository into `Item`s
- `models.py`: `Item`, `RepoSnapshot` and the enums
- `triage.py`: which inbox section an item belongs to, and its marker time. Pure functions
- `store.py`: SQLite snapshots, seen, snooze and notified marks, inbox and overview queries
- `poller.py`: one poll at a time, a background thread, listeners for new entries
- `i18n.py` and `locales/*.json`: translations. All catalogs carry the same keys
- `notify.py`: notification texts and backends. `tray.py`: menu bar. `window.py`: pywebview window
- `web/app.py`: routes; `web/templates`: Jinja2; `web/static`: CSS and htmx

Tests mirror the modules in `tests/test_<module>.py`. `tests/fakes.py` fakes `gh` by argument match.

## Code Style

- Strict mypy typing. Every function has annotations
- Start each module with `from __future__ import annotations`
- Import abstract types from `collections.abc`
- Inject side effects (the `gh` runner, the clock) so tests run without GitHub
- Code that needs the menu bar or a real window carries `# pragma: no cover` and stays thin

## Review Focus

Flag these in a pull request:

- A `gh` call outside `gh.py`, or a GitHub token read, stored or logged by the app
- A web route without the local host and origin check, or a server bound to anything but `127.0.0.1`
- User data rendered without Jinja2 autoescaping, or `Markup` around untrusted text
- A new user-facing string that is not in all three catalogs (`en`, `ru`, `de`)
- A change to the inbox rules in `triage.py` without a test for each section it touches
- A first poll that sends notifications
- New runtime dependencies without a reason
- Missing tests for new behavior, or coverage below 90%
- No `## [Unreleased]` entry in `CHANGELOG.md`, or README not updated for a behavior change

Commit, branch and pull request rules are in `CONTRIBUTING.md`.
