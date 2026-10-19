"""The app window: the local web UI in a native pywebview window, or the browser as fallback."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urljoin

WIDTH, HEIGHT = 1200, 820
SAVE_DELAY_SECONDS = 0.5
Geometry = dict[str, int | None]


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


def load_geometry(base_url: str) -> Geometry:
    """The saved window size and position, or the defaults when the server does not answer."""
    try:
        with urllib.request.urlopen(urljoin(base_url, "/window"), timeout=2) as response:
            return dict(json.load(response))
    except OSError, ValueError:
        return {"width": WIDTH, "height": HEIGHT, "x": None, "y": None}


def save_geometry(base_url: str, geometry: Geometry) -> None:
    request = urllib.request.Request(
        urljoin(base_url, "/window"),
        data=json.dumps(geometry).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2):
            pass
    except OSError:
        pass  # the size is a convenience; a missed save must not disturb the window


class GeometrySaver:
    """Collects resize and move events and saves once they settle, not on every pixel."""

    def __init__(self, save: Callable[[Geometry], None], delay: float = SAVE_DELAY_SECONDS) -> None:
        self._save = save
        self._delay = delay
        self._pending: Geometry = {}
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def resized(self, width: float, height: float) -> None:
        self._update({"width": round(width), "height": round(height)})  # AppKit reports points as floats

    def moved(self, x: float, y: float) -> None:
        self._update({"x": round(x), "y": round(y)})

    def _update(self, values: Geometry) -> None:
        with self._lock:
            self._pending.update(values)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
        if pending:
            self._save(pending)


def main(argv: Sequence[str] | None = None) -> None:  # pragma: no cover - opens a native window
    url = (argv or sys.argv[1:])[0]
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        return
    from repoboard.macos import brand_process

    brand_process(menu_bar_only=False)
    geometry: dict[str, Any] = load_geometry(url)
    window = webview.create_window(
        "Repoboard", url, width=geometry["width"], height=geometry["height"], x=geometry["x"], y=geometry["y"]
    )
    assert window is not None
    saver = GeometrySaver(lambda values: save_geometry(url, values))
    window.events.resized += saver.resized
    window.events.moved += saver.moved
    window.events.closing += saver.flush
    webview.start()


if __name__ == "__main__":  # pragma: no cover
    main()
