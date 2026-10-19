"""The app window: the local web UI in a native pywebview window, or the browser as fallback."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from collections.abc import Callable, Sequence
from typing import BinaryIO
from urllib.parse import urljoin

WIDTH, HEIGHT = 1200, 820
SAVE_DELAY_SECONDS = 0.5
Geometry = dict[str, int]


CLOSE_TIMEOUT_SECONDS = 3


class Window:
    """Runs the window in a child process: pywebview and the menu bar both need the main thread.

    The parent talks to the child through its stdin: "focus" brings the window forward, "close" closes
    it the normal way, so the window still saves its size. A closed stdin closes the window too.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self._process: subprocess.Popen[bytes] | None = None

    def open(self) -> None:
        if self._alive() and self._send(b"focus"):
            return
        self._stop()
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-m", "repoboard.window", self.url], stdin=subprocess.PIPE
            )
        except OSError:
            webbrowser.open(self.url)

    def close(self) -> None:
        if self._alive():
            self._send(b"close")
            self._stop()

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _send(self, command: bytes) -> bool:
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(command + b"\n")
            self._process.stdin.flush()
        except OSError, ValueError:
            return False
        return True

    def _stop(self) -> None:
        """Wait for a window that was asked to close, and end it when it does not."""
        if not self._alive():
            return
        assert self._process is not None
        try:
            self._process.wait(timeout=CLOSE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.terminate()


def follow_commands(stream: BinaryIO, focus: Callable[[], object], close: Callable[[], object]) -> None:
    """Run the parent's commands until "close" or the end of the stream, then close the window."""
    for line in stream:
        if line.strip() != b"focus":
            break  # "close", or a command this version does not know
        focus()
    close()


def load_geometry(base_url: str) -> Geometry:
    """The saved window size, or the default when the server does not answer.

    Only the size is kept. A saved position can point at a monitor that is gone."""
    try:
        with urllib.request.urlopen(urljoin(base_url, "/window"), timeout=2) as response:
            return dict(json.load(response))
    except OSError, ValueError:
        return {"width": WIDTH, "height": HEIGHT}


def save_geometry(base_url: str, geometry: Geometry) -> None:
    request = urllib.request.Request(
        urljoin(base_url, "/window"),
        data=json.dumps(geometry).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # The size is a convenience; a missed save must not disturb the window.
    with contextlib.suppress(OSError):
        urllib.request.urlopen(request, timeout=1).close()


class GeometrySaver:
    """Collects resize events and saves once they settle, not on every pixel."""

    def __init__(
        self,
        save: Callable[[Geometry], None],
        delay: float = SAVE_DELAY_SECONDS,
        title_bar: float = 0,
        skip: Callable[[], bool] = lambda: False,
    ) -> None:
        self._save = save
        self._skip = skip  # true in fullscreen: that size is not one the user chose
        # pywebview reports the frame height on resize but opens a window with that height as content.
        self._title_bar = title_bar
        self._delay = delay
        self._pending: Geometry = {}
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def resized(self, width: float, height: float) -> None:
        if self._skip():
            return
        # AppKit reports points as floats.
        self._update({"width": round(width), "height": round(height - self._title_bar)})

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
    from repoboard.macos import brand_process, is_fullscreen, title_bar_height

    brand_process(menu_bar_only=False)
    size = load_geometry(url)
    window = webview.create_window("Repoboard", url, width=size["width"], height=size["height"])
    assert window is not None
    saver = GeometrySaver(
        lambda values: save_geometry(url, values), title_bar=title_bar_height(), skip=lambda: is_fullscreen(window)
    )
    window.events.resized += saver.resized
    window.events.closing += saver.flush

    def focus() -> None:
        window.restore()
        window.show()

    threading.Thread(
        target=follow_commands, args=(sys.stdin.buffer, focus, window.destroy), name="repoboard-commands", daemon=True
    ).start()
    webview.start()


if __name__ == "__main__":  # pragma: no cover
    main()
