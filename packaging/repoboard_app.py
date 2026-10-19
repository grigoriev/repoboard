"""Entry point of Repoboard.app. Without arguments the app runs in the menu bar."""

from __future__ import annotations

import sys

from repoboard.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tray"]))
