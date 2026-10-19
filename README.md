# repoboard

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Repoboard gives a daily overview of the GitHub repositories you work with. It lives in the macOS
menu bar, polls the repositories through the `gh` CLI and notifies you about what needs you:

- new issues, discussions and pull requests from other people;
- new replies, also in closed issues and discussions;
- open security alerts: Dependabot, code scanning and secret scanning;
- Renovate pull requests with green checks that wait for an approval or have no automerge;
- pull requests with failed checks or conflicts.

The **New** tab lists what changed since you last looked. Mark an item seen: it comes back when
someone acts on it again. Or snooze it for a day or a week: it comes back when the snooze ends. The
**Overview** tab shows every repository with its open issues, discussions, pull requests and alerts.
Click a column header to sort by it. A page per repository lists the items.

The UI speaks English, Russian and German. Change the text size with A- and A+ in the header, or
with Cmd or Ctrl and +, - or 0.

## Requirements

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- [GitHub CLI](https://cli.github.com/), logged in: `gh auth login`
- macOS for the menu bar. On other systems use `repoboard serve`.

## Install

```sh
uv tool install git+https://github.com/grigoriev/repoboard
```

## Configure

Copy [config.example.toml](config.example.toml) to `~/.config/repoboard/config.toml` and list your
repositories. To import the visible repositories of the Alfred GitHub workflow:

```sh
repoboard import-alfred
```

## Run

| Command | Effect |
|---|---|
| `repoboard` | Menu bar app on macOS: polls, notifies, opens the window. |
| `repoboard serve` | Polls and serves the UI on `http://127.0.0.1:8765/` in the browser. |
| `repoboard poll` | Polls once and prints the number of items per section. |
| `repoboard import-alfred` | Adds the repositories of the Alfred GitHub workflow to the config. |

The first poll looks back one week and sends no notifications. Later polls notify about each new
item, or send one summary when more than three arrive at once.

## Security alerts

GitHub shows security alerts only to people with admin or security rights on a repository. Where
Repoboard has no access, the overview shows a dash and the repository page names the reason.

## Notifications on macOS

Clickable native notifications need an app bundle. Started from the terminal, Repoboard notifies
through `osascript`, and a click on the notification does not open the item.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately, see [SECURITY.md](SECURITY.md).

## Disclaimer

This software is provided "as is", without warranty of any kind, as the LICENSE states. Use it at
your own risk. Sergey Grigoriev is not liable for damage from its use, as far as the law allows. It
is published free of charge, outside of any commercial offering, with no obligation to support it.
Security reports are welcome, see SECURITY.md.

## License

Apache 2.0, see [LICENSE](LICENSE).
