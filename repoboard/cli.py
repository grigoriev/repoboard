"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from collections.abc import Sequence

from repoboard import __version__, config
from repoboard.gh import Gh
from repoboard.poller import Poller
from repoboard.store import Store
from repoboard.triage import Section


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repoboard", description="Daily overview of your GitHub repositories.")
    parser.add_argument("--version", action="version", version=f"repoboard {__version__}")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="poll in the background and serve the web UI")
    serve.add_argument("--no-browser", action="store_true", help="do not open the browser")
    commands.add_parser("poll", help="poll once and print the inbox counts")
    commands.add_parser("import-alfred", help="add the repositories of the Alfred GitHub workflow to the config")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"repoboard: {exc}", file=sys.stderr)
        return 2
    if args.command == "import-alfred":
        return import_alfred(cfg)
    if args.command == "poll":
        return poll_once(cfg)
    if args.command == "serve":
        return serve(cfg, open_browser=not args.no_browser)
    build_parser().print_help()
    return 0


def import_alfred(cfg: config.Config) -> int:
    repos = config.read_alfred_repos()
    updated = config.with_repos(cfg, repos)
    config.save(updated)
    print(f"{len(updated.repos) - len(cfg.repos)} added, {len(updated.repos)} in {config.config_path()}")
    return 0


def poll_once(cfg: config.Config) -> int:
    store = Store(cfg.database)
    try:
        poller = Poller(cfg, store, Gh())
        poller.poll()
        if poller.last_error:
            print(f"repoboard: {poller.last_error}", file=sys.stderr)
            return 1
        inbox = store.inbox(poller.clock())
        for section in Section:
            print(f"{section.value:10} {len(inbox[section])}")
        return 0
    finally:
        store.close()


def serve(cfg: config.Config, open_browser: bool) -> int:  # pragma: no cover - runs a server until interrupted
    import uvicorn

    from repoboard.web.app import create_app

    store = Store(cfg.database)
    poller = Poller(cfg, store, Gh())
    poller.start()
    url = f"http://127.0.0.1:{cfg.port}/"
    if open_browser:
        webbrowser.open(url)
    try:
        uvicorn.run(create_app(poller), host="127.0.0.1", port=cfg.port, log_level="warning")
    finally:
        poller.stop()
        store.close()
    return 0
