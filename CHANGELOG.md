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
- The window keeps its size between sessions. It opens centered, so it never lands on a monitor
  that is gone.
- Opening an item on GitHub marks it seen and polls its repository again. Coming back to
  Repoboard polls those repositories at once, so a merged pull request leaves the list in seconds.
- The page reloads its content in place after every action and poll. Section counts stay right.
- The number of an issue or pull request links to it. Item tables share their column widths.
- Row actions stay readable in every language: shorter snooze labels with the full text as a hint.
  Column widths are relative, so large text keeps room for the title.
- `hidden_alert_tools` in the config leaves out the alerts of chosen code scanning tools. Scorecard
  is hidden by default.
- Keyboard focus survives live updates. After "Seen" it moves to the same button of the next row.
- The menu bar asks the window to close instead of killing it, so the window keeps its size. A
  window whose menu bar process is gone closes as well.
- `scripts/build-app.sh` builds `Repoboard.app`, a menu bar app with **Open at login** in its menu.
- Notifications fall back to terminal-notifier, which opens the item on a click, when macOS does not
  allow native notifications.
- Row actions are icon buttons: seen, snooze one day and snooze one week. The full text is a hint.
- `repoboard tray` runs in the Windows and Linux tray, with the unseen count as a badge on the icon.
- `[[owners]]` in the config watches every repository of a user or organization, narrowed by
  name patterns and repository types. New repositories join within an hour and are announced.
