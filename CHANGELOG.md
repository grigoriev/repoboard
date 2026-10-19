# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffold: uv, ruff, mypy, pytest.
- TOML config with the repositories, language and poll interval, and an import from the Alfred
  GitHub workflow list.
- Collector over the `gh` CLI: issues, pull requests, discussions and security alerts of each
  repository, with the latest reply by someone else.
- SQLite store with inbox sections (security, failed, Renovate, new, replies), seen and snooze
  marks, notification tracking and a per-repository overview.
- English, Russian and German translations. English is the default.
