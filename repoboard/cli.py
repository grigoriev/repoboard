"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import socket
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
    commands.add_parser("tray", help="run in the menu bar with notifications (the default on macOS)")
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
    if args.command == "tray" or (args.command is None and sys.platform == "darwin"):
        return tray(cfg)
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


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def serve(cfg: config.Config, open_browser: bool) -> int:  # pragma: no cover - runs a server until interrupted
    import uvicorn

    from repoboard.web.app import create_app

    if not port_free(cfg.port):
        print(f"repoboard: port {cfg.port} is busy. Is Repoboard already running?", file=sys.stderr)
        return 1
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


def tray(cfg: config.Config) -> int:  # pragma: no cover - needs the macOS menu bar
    import threading

    import uvicorn

    from repoboard.i18n import Translator
    from repoboard.notify import NotificationListener, default_notifier
    from repoboard.tray import run_tray
    from repoboard.web.app import create_app
    from repoboard.window import Window

    if not port_free(cfg.port):
        print(f"repoboard: port {cfg.port} is busy. Is Repoboard already running?", file=sys.stderr)
        return 1
    store = Store(cfg.database)
    poller = Poller(cfg, store, Gh())
    url = f"http://127.0.0.1:{cfg.port}/"

    def translator() -> Translator:
        return Translator(store.get_setting("language") or cfg.language)

    poller.listeners.append(NotificationListener(default_notifier(), translator, url))
    server = uvicorn.Server(uvicorn.Config(create_app(poller), host="127.0.0.1", port=cfg.port, log_level="warning"))
    threading.Thread(target=server.run, name="repoboard-web", daemon=True).start()
    poller.start()
    window = Window(url)

    def quit_app() -> None:
        poller.stop()
        server.should_exit = True
        window.close()

    run_tray(poller, translator, window.open, quit_app)
    store.close()
    return 0
