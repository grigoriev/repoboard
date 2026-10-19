"""The app window: the local web UI in a native pywebview window, or the browser as fallback."""

from __future__ import annotations

import subprocess
import sys
import webbrowser
from collections.abc import Sequence

WIDTH, HEIGHT = 1200, 820


class Window:
    """Runs the window in a child process: pywebview and the menu bar both need the main thread."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._process: subprocess.Popen[bytes] | None = None

    def open(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()  # reopening brings the window to the front
        try:
            self._process = subprocess.Popen([sys.executable, "-m", "repoboard.window", self.url])
        except OSError:
            webbrowser.open(self.url)

    def close(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - opens a native window
    url = (argv or sys.argv[1:])[0]
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        return
    webview.create_window("Repoboard", url, width=WIDTH, height=HEIGHT)
    webview.start()


if __name__ == "__main__":  # pragma: no cover
    main()
