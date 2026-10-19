# repoboard

[![CI](https://github.com/grigoriev/repoboard/actions/workflows/ci.yml/badge.svg)](https://github.com/grigoriev/repoboard/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/grigoriev/repoboard/badge)](https://scorecard.dev/viewer/?uri=github.com/grigoriev/repoboard)
[![Release](https://img.shields.io/github/v/release/grigoriev/repoboard)](https://github.com/grigoriev/repoboard/releases)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=grigoriev_repoboard&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=grigoriev_repoboard)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=grigoriev_repoboard&metric=coverage)](https://sonarcloud.io/summary/new_code?id=grigoriev_repoboard)

Repoboard gives a daily overview of the GitHub repositories you work with. It lives in the macOS
menu bar, polls the repositories through the `gh` CLI and notifies you about what needs you:

- new issues, discussions and pull requests from other people;
- new replies, also in closed issues and discussions;
- open security alerts: Dependabot, code scanning and secret scanning;
- Renovate pull requests with green checks that wait for an approval or have no automerge;
- pull requests with failed checks or conflicts.

The **New** tab lists what changed since you last looked. Opening an item marks it seen: it comes
back when someone acts on it again. Repoboard polls the repositories you opened items of again
when you come back to it, so a pull request you merged leaves the list at once. Or snooze it for a day or a week: it comes back when the snooze ends. The
**Overview** tab shows every repository with its open issues, discussions, pull requests and alerts.
Click a column header to sort by it. A page per repository lists the items.

The UI speaks English, Russian and German. Change the text size with A- and A+ in the header, or
with Cmd or Ctrl and +, - or 0.

## Requirements

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- [GitHub CLI](https://cli.github.com/), logged in: `gh auth login`
- macOS, Windows or Linux. The tray needs a desktop with a system tray; `repoboard serve` runs
  anywhere. On Linux the tray menu needs PyGObject, which builds against the system GTK and
  AppIndicator: install those (`libgirepository1.0-dev`, `gir1.2-ayatanaappindicator3-0.1` on
  Debian and Ubuntu) and then `uv tool install "repoboard[linux] @ git+https://github.com/grigoriev/repoboard"`.
  Without a native window toolkit the window opens in the browser.

## Install

```sh
uv tool install git+https://github.com/grigoriev/repoboard
```

Or install a release: download the wheel from the
[releases page](https://github.com/grigoriev/repoboard/releases) and run `uv tool install <wheel>`.

### Verify

Every release carries a signed provenance bundle. Verify a downloaded file with
`gh attestation verify <file> --repo grigoriev/repoboard`.

### macOS app

Build `Repoboard.app` and move it to `/Applications`:

```sh
scripts/build-app.sh
mv dist/Repoboard.app /Applications/
```

The app lives in the menu bar. Its menu has **Open at login**. The build is signed ad hoc, not
with an Apple Developer ID, so macOS asks once whether Repoboard may send notifications.

## Configure

Open **Settings** in the app, or copy [config.example.toml](config.example.toml) to
`~/.config/repoboard/config.toml`. The settings page saves that file and applies it at once; the eye
button on a row watches or unwatches that item. Watch whole owners, single repositories, or both:

- `[[owners]]` takes every repository of a user or organization. New repositories join on their
  own, and Repoboard announces them. `include` and `exclude` narrow the list by name patterns,
  `exclude_types` by type: archived, fork, mirror, template, private, public.
- `repos` lists single repositories.
- `items` lists single pull requests, issues and discussions by their links, in any repository.
  They have their own section, **Watched**, and show up there on every change: a reply, a merge,
  a failed check. `repoboard watch <link>` and `repoboard unwatch <link>` edit the list.

To import the visible repositories of the Alfred GitHub workflow:

```sh
repoboard import-alfred
```

## Run

| Command | Effect |
|---|---|
| `repoboard` | Menu bar app on macOS: polls, notifies, opens the window. |
| `repoboard tray` | The same in the Windows or Linux tray, with the unseen count as a badge. |
| `repoboard serve` | Polls and serves the UI on `http://127.0.0.1:8765/` in the browser. |
| `repoboard poll` | Polls once and prints the number of items per section. |
| `repoboard import-alfred` | Adds the repositories of the Alfred GitHub workflow to the config. |
| `repoboard watch <link>` | Watches one pull request, issue or discussion. `unwatch` stops it. |

The first poll looks back one week and sends no notifications. Later polls notify about each new
item, or send one summary when more than three arrive at once.

## Security alerts

GitHub shows security alerts only to people with admin or security rights on a repository. Where
Repoboard has no access, the overview shows a dash and the repository page names the reason.

Scorecard findings are about repository hygiene, not vulnerabilities, so Repoboard hides them by
default. List the tools to hide in `hidden_alert_tools`, or set it to `[]` to see every alert.

## Notifications on macOS

Repoboard picks the first way that works:

1. Native notifications of `Repoboard.app`, once you allow them. A click opens the item.
2. [terminal-notifier](https://github.com/julienXX/terminal-notifier)
   (`brew install terminal-notifier`). A click opens the item.
3. `osascript`. A click does not open the item.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately, see [SECURITY.md](SECURITY.md).

## Disclaimer

This software is provided "as is", without warranty of any kind, as the LICENSE states. Use it at
your own risk. Sergey Grigoriev is not liable for damage from its use, as far as the law allows. It
is published free of charge, outside of any commercial offering, with no obligation to support it.
Security reports are welcome, see SECURITY.md.

## License

MIT, see [LICENSE](LICENSE).
