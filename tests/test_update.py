from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from repoboard import update
from repoboard.gh import Gh, GhError
from repoboard.store import Store
from repoboard.update import (
    APP_NAME,
    NOTIFIED_SETTING,
    SWAP_SCRIPT,
    Release,
    Updater,
    UpdateState,
    app_updater,
    asset_name,
    bundle_path,
    newer_release,
    parse_version,
)
from tests.fakes import FakeGhRunner

ASSET = "Repoboard-0.2.0-macos-arm64.zip"
RELEASE = Release("0.2.0", "https://github.com/grigoriev/repoboard/releases/tag/v0.2.0", ASSET)


def latest(tag: str = "v0.2.0", assets: tuple[str, ...] = (ASSET,)) -> dict[str, Any]:
    return {"tag_name": tag, "html_url": RELEASE.url, "assets": [{"name": name} for name in assets]}


@pytest.fixture
def store() -> Iterator[Store]:
    db = Store(":memory:")
    yield db
    db.close()


def updater(store: Store, tmp_path: Path, routes: dict[str, Any], **kwargs: Any) -> tuple[Updater, list[Release]]:
    notified: list[Release] = []
    bundle = tmp_path / "Applications" / APP_NAME
    bundle.mkdir(parents=True)
    kwargs.setdefault("current", "0.1.0")
    kwargs.setdefault("arch", "arm64")
    return Updater(Gh(FakeGhRunner(routes)), store, bundle, notified.append, **kwargs), notified


@pytest.mark.parametrize(
    ("text", "version"), [("v1.2.3", (1, 2, 3)), ("0.10.0", (0, 10, 0)), ("v1.2", None), ("nightly", None)]
)
def test_parse_version(text: str, version: tuple[int, ...] | None) -> None:
    assert parse_version(text) == version


def test_asset_name() -> None:
    assert asset_name("0.2.0", "arm64") == ASSET


def test_newer_release() -> None:
    assert newer_release(latest(), "0.1.0", "arm64") == RELEASE
    # 0.10.0 is newer than 0.9.0: versions compare as numbers, not as text.
    assert newer_release(latest("v0.10.0", ("Repoboard-0.10.0-macos-arm64.zip",)), "0.9.0", "arm64") is not None


@pytest.mark.parametrize(
    ("data", "current", "arch"),
    [
        (latest(), "0.2.0", "arm64"),  # the same version
        (latest(), "0.3.0", "arm64"),  # an older release
        (latest(), "0.1.0", "x86_64"),  # no app for the architecture
        (latest("nightly"), "0.1.0", "arm64"),
        (latest(), "dev", "arm64"),
        ({"tag_name": "v0.2.0"}, "0.1.0", "arm64"),  # no assets
        (None, "0.1.0", "arm64"),
    ],
)
def test_no_newer_release(data: Any, current: str, arch: str) -> None:
    assert newer_release(data, current, arch) is None


def test_bundle_path(tmp_path: Path) -> None:
    executable = tmp_path / APP_NAME / "Contents" / "MacOS" / "Repoboard"
    assert bundle_path(str(executable)) == (tmp_path / APP_NAME).resolve()
    assert bundle_path(str(tmp_path / "bin" / "python" / "x" / "y")) is None
    assert bundle_path("/python") is None


def test_check_notifies_once_per_version(store: Store, tmp_path: Path) -> None:
    up, notified = updater(store, tmp_path, {"releases/latest": latest()})
    assert up.check() == RELEASE
    assert up.check() == RELEASE
    assert up.available == RELEASE
    assert notified == [RELEASE]
    assert store.get_setting(NOTIFIED_SETTING) == "0.2.0"


def test_check_without_a_newer_release(store: Store, tmp_path: Path) -> None:
    up, notified = updater(store, tmp_path, {"releases/latest": latest("v0.1.0")})
    assert up.check() is None
    assert notified == []


def test_check_failure_keeps_the_known_release(store: Store, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    up, _ = updater(store, tmp_path, {"releases/latest": GhError("offline")})
    up.available = RELEASE
    assert up.check() == RELEASE
    assert "update check failed: offline" in caplog.text


def fake_ditto(app: bool = True) -> Any:
    def run(args: list[str], **_: Any) -> None:
        assert args[:3] == ["ditto", "-x", "-k"]
        if app:
            (Path(args[4]) / APP_NAME / "Contents").mkdir(parents=True)

    return run


def download(args: list[str]) -> str:
    (Path(args[args.index("--dir") + 1]) / ASSET).write_bytes(b"zip")
    return ""


def test_prepare_downloads_verifies_and_unpacks(store: Store, tmp_path: Path) -> None:
    routes = {"release download": download, "attestation verify": ""}
    up, _ = updater(store, tmp_path, routes, run=fake_ditto())
    up.available = RELEASE
    up.prepare()
    assert up.state is UpdateState.READY
    [work] = [path for path in up.bundle.parent.iterdir() if path.name.startswith(".repoboard-update-")]
    assert (work / APP_NAME).is_dir()
    assert not (work / ASSET).exists()
    calls = up.gh._runner.calls  # type: ignore[attr-defined]
    assert calls[0][:3] == ["release", "download", "v0.2.0"]
    assert calls[1] == ["attestation", "verify", str(work / ASSET), "--repo", "grigoriev/repoboard"]
    command = up.swap_command(42)
    assert command[:3] == ["/bin/bash", "-c", SWAP_SCRIPT]
    assert command[4:] == ["42", str(work / APP_NAME), str(up.bundle), str(work)]


@pytest.mark.parametrize(
    ("routes", "app"),
    [
        ({"release download": download, "attestation verify": GhError("no attestation")}, True),
        ({"release download": GhError("not found")}, True),
        ({"release download": download, "attestation verify": ""}, False),  # the zip holds no app
    ],
)
def test_prepare_failure_cleans_up(
    store: Store, tmp_path: Path, caplog: pytest.LogCaptureFixture, routes: dict[str, Any], app: bool
) -> None:
    up, _ = updater(store, tmp_path, routes, run=fake_ditto(app))
    up.available = RELEASE
    up.prepare()
    assert up.state is UpdateState.FAILED
    assert [path.name for path in up.bundle.parent.iterdir()] == [APP_NAME]
    assert "update to 0.2.0 failed" in caplog.text


def test_prepare_failure_without_a_writable_folder(store: Store, tmp_path: Path) -> None:
    up, _ = updater(store, tmp_path, {})
    up.bundle = tmp_path / "missing" / APP_NAME
    up.available = RELEASE
    up.prepare()
    assert up.state is UpdateState.FAILED


def test_install_runs_once(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up, _ = updater(store, tmp_path, {})
    started: list[str] = []
    monkeypatch.setattr(up, "prepare", lambda: started.append("prepare"))
    up.install()  # nothing to install yet
    up.available = RELEASE
    up.install()
    up.install()  # already installing
    up.state = UpdateState.FAILED
    up.install()  # a retry after a failure
    for thread in threading.enumerate():
        if thread.name == "repoboard-update":
            thread.join()
    assert started == ["prepare", "prepare"]


def test_swap_starts_the_script_in_its_own_session(store: Store, tmp_path: Path) -> None:
    spawned: list[tuple[list[str], dict[str, Any]]] = []
    up, _ = updater(store, tmp_path, {}, spawn=lambda args, **kwargs: spawned.append((args, kwargs)))
    up._work = tmp_path / "work"
    up.swap()
    [(args, kwargs)] = spawned
    assert args[4] == str(os.getpid())
    assert kwargs == {"start_new_session": True}


def run_swap(tmp_path: Path, app: Path, new: Path, work: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    opened = tmp_path / "opened"
    fake_open = bin_dir / "open"
    fake_open.write_text(f'#!/bin/sh\necho "$1" > {opened}\n')
    fake_open.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    # 999999 stands for the app process, which has ended.
    args = ["/bin/bash", "-c", SWAP_SCRIPT, "swap", "999999", str(new), str(app), str(work)]
    subprocess.run(args, check=True, env=env, timeout=10)
    return opened


def test_swap_script_replaces_the_bundle(tmp_path: Path) -> None:
    app, work = tmp_path / APP_NAME, tmp_path / ".work"
    (app / "old").mkdir(parents=True)
    (work / APP_NAME / "new").mkdir(parents=True)
    opened = run_swap(tmp_path, app, work / APP_NAME, work)
    assert (app / "new").is_dir()
    assert not (app / "old").exists()
    assert not work.exists()
    assert opened.read_text().strip() == str(app)


def test_swap_script_restores_the_bundle(tmp_path: Path) -> None:
    app, work = tmp_path / APP_NAME, tmp_path / ".work"
    (app / "old").mkdir(parents=True)
    work.mkdir()
    opened = run_swap(tmp_path, app, work / APP_NAME, work)  # the new bundle is missing
    assert (app / "old").is_dir()
    assert opened.read_text().strip() == str(app)


def test_check_loop_stops(store: Store, tmp_path: Path) -> None:
    up, notified = updater(store, tmp_path, {"releases/latest": latest()})
    up.stop()
    up.start()
    for thread in threading.enumerate():
        if thread.name == "repoboard-update-check":
            thread.join()
    assert notified == [RELEASE]


def test_app_updater(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = str(tmp_path / APP_NAME / "Contents" / "MacOS" / "Repoboard")
    monkeypatch.setattr(update, "in_app_bundle", lambda: False)
    assert app_updater(store, print, executable) is None
    monkeypatch.setattr(update, "in_app_bundle", lambda: True)
    assert app_updater(store, print, str(tmp_path / "python")) is None
    made = app_updater(store, print, executable)
    assert made is not None
    assert made.bundle == (tmp_path / APP_NAME).resolve()
