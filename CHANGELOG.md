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
- Background poller: polls every interval or on demand and reports new items. The first poll
  stays silent.
- Local web UI on 127.0.0.1: the inbox with seen and snooze actions, an overview of every
  repository, a page per repository and a language switch.
- Commands `repoboard serve`, `repoboard poll` and `repoboard import-alfred`.
- Menu bar mode, the default on macOS: unseen count on the icon, sections in the menu, refresh,
  a native window with pywebview, and desktop notifications about new items.
- Text size control: A- and A+ in the header, or Cmd or Ctrl with +, - and 0. The UI uses the
  full window width, and long repository names wrap instead of being cut.
- App icon. The window shows it and the name Repoboard in Cmd-Tab and the Dock. The menu bar
  process stays out of Cmd-Tab.
- Sort the overview by any column. The sort is kept between sessions.
- The window keeps its size and position between sessions.
