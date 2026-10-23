"""Updates of Repoboard.app: find a newer release, then download, verify and swap the app bundle."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from repoboard import __version__
from repoboard.gh import Gh, GhError
from repoboard.macos import in_app_bundle
from repoboard.store import Store

log = logging.getLogger(__name__)

REPO = "grigoriev/repoboard"
APP_NAME = "Repoboard.app"
CHECK_SECONDS = 6 * 60 * 60
NOTIFIED_SETTING = "update_notified"  # the last version the user got a notification about
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# Runs after the app quits: wait for its process to end, swap the bundles, start the new app.
# A failed swap puts the old bundle back.
SWAP_SCRIPT = """\
pid="$1" new="$2" app="$3" work="$4"
while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
if mv "$app" "$work/old.app"; then
    mv "$new" "$app" || mv "$work/old.app" "$app"
fi
open "$app"
rm -rf "$work"
"""


@dataclass(frozen=True)
class Release:
    version: str
    url: str  # the release page
    asset: str  # file name of the app zip


class UpdateState(StrEnum):
    IDLE = "idle"
    INSTALLING = "installing"
    READY = "ready"  # downloaded and verified: the swap waits for the app to quit
    FAILED = "failed"


def parse_version(text: str) -> tuple[int, ...] | None:
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def asset_name(version: str, arch: str) -> str:
    return f"Repoboard-{version}-macos-{arch}.zip"


def newer_release(data: Any, current: str, arch: str) -> Release | None:
    """The release from the GitHub API, if it is newer than `current` and has an app for `arch`."""
    if not isinstance(data, dict):
        return None
    latest = parse_version(str(data.get("tag_name", "")))
    running = parse_version(current)
    if latest is None or running is None or latest <= running:
        return None
    version = ".".join(map(str, latest))
    name = asset_name(version, arch)
    if not any(isinstance(asset, dict) and asset.get("name") == name for asset in data.get("assets") or []):
        return None
    return Release(version, str(data.get("html_url", f"https://github.com/{REPO}/releases")), name)


def bundle_path(executable: str = sys.executable) -> Path | None:
    """The .app bundle around the executable: Repoboard.app/Contents/MacOS/Repoboard."""
    parents = Path(executable).resolve().parents
    return parents[2] if len(parents) > 2 and parents[2].suffix == ".app" else None


class Updater:
    def __init__(
        self,
        gh: Gh,
        store: Store,
        bundle: Path,
        notify: Callable[[Release], None],
        current: str = __version__,
        arch: str = platform.machine(),
        spawn: Callable[..., object] = subprocess.Popen,
        run: Callable[..., object] = subprocess.run,
    ) -> None:
        self.gh = gh
        self.store = store
        self.bundle = bundle
        self.notify = notify
        self.current = current
        self.arch = arch
        self.spawn = spawn
        self.run = run
        self.available: Release | None = None
        self.state = UpdateState.IDLE
        self._work: Path | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def check(self) -> Release | None:
        """Look for a newer release. The user gets one notification per version."""
        try:
            release = newer_release(self.gh.latest_release(REPO), self.current, self.arch)
        except GhError as exc:
            log.info("update check failed: %s", exc)
            return self.available
        self.available = release
        if release and self.store.get_setting(NOTIFIED_SETTING) != release.version:
            self.store.set_setting(NOTIFIED_SETTING, release.version)
            self.notify(release)
        return release

    def install(self) -> None:
        """Download and verify the available release in the background. The state turns READY or FAILED."""
        with self._lock:
            if self.available is None or self.state in (UpdateState.INSTALLING, UpdateState.READY):
                return
            self.state = UpdateState.INSTALLING
        threading.Thread(target=self.prepare, name="repoboard-update", daemon=True).start()

    def prepare(self) -> None:
        release = self.available
        assert release is not None
        work: Path | None = None
        try:
            # Next to the bundle: the swap is then a rename on one volume.
            work = Path(tempfile.mkdtemp(prefix=".repoboard-update-", dir=self.bundle.parent))
            archive = work / release.asset
            self.gh.download_asset(REPO, f"v{release.version}", release.asset, str(work))
            self.gh.verify_attestation(str(archive), REPO)
            self.run(["ditto", "-x", "-k", str(archive), str(work)], check=True, capture_output=True)
            archive.unlink()
            if not (work / APP_NAME).is_dir():
                raise OSError(f"{release.asset} holds no {APP_NAME}")
        except Exception as exc:  # any failure must leave a state the user can retry from
            log.warning("update to %s failed: %s", release.version, exc)
            if work:
                shutil.rmtree(work, ignore_errors=True)
            self.state = UpdateState.FAILED
            return
        self._work = work
        self.state = UpdateState.READY

    def swap_command(self, pid: int) -> Sequence[str]:
        assert self._work is not None
        new, work = str(self._work / APP_NAME), str(self._work)
        return ["/bin/bash", "-c", SWAP_SCRIPT, "repoboard-update", str(pid), new, str(self.bundle), work]

    def swap(self) -> None:
        """Start the swap. It waits until this process ends, so the caller quits the app next."""
        self.spawn(self.swap_command(os.getpid()), start_new_session=True)

    def start(self) -> None:
        threading.Thread(target=self._run, name="repoboard-update-check", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while True:
            try:
                self.check()
            except Exception:  # an unexpected failure must not end the background loop
                log.exception("update check failed")
            if self._stop.wait(CHECK_SECONDS):
                return


def app_updater(store: Store, notify: Callable[[Release], None], executable: str = sys.executable) -> Updater | None:
    """An updater for Repoboard.app. A run from source or a wheel updates through its package manager."""
    bundle = bundle_path(executable)
    if not in_app_bundle() or bundle is None:
        return None
    return Updater(Gh(), store, bundle, notify)
