"""Entry point of Repoboard.app. Without arguments the app runs in the menu bar."""

from __future__ import annotations

import os
import sys

from repoboard.cli import main
from repoboard.macos import app_path

if __name__ == "__main__":
    # launchd starts the app with a bare PATH: without this, gh and terminal-notifier are not found.
    os.environ["PATH"] = app_path(os.environ.get("PATH", ""))
    sys.exit(main(sys.argv[1:] or ["tray"]))
