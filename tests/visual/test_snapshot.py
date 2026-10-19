"""Unit tests of the screenshot comparison. They run with the visual tests, which bring Pillow."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from tests.visual import snapshot

pytestmark = pytest.mark.visual


def png(
    color: tuple[int, int, int], size: tuple[int, int] = (40, 30), dot: tuple[int, int, int] | None = None
) -> bytes:
    image = Image.new("RGB", size, color)
    if dot:
        for x in range(3):
            image.putpixel((x, 0), dot)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(snapshot, "SNAPSHOTS", tmp_path / "snapshots")
    monkeypatch.setattr(snapshot, "OUTPUT", tmp_path / "output")
    monkeypatch.delenv("UPDATE_SNAPSHOTS", raising=False)
    return tmp_path


def test_a_change_in_one_channel_fails(dirs: Path) -> None:
    (dirs / "snapshots").mkdir()
    (dirs / "snapshots" / "v.png").write_bytes(png((100, 100, 100)))
    with pytest.raises(AssertionError, match="3 pixels changed"):
        snapshot.assert_matches("v", png((100, 100, 100), dot=(100, 100, 220)))
    assert (dirs / "output" / "v.actual.png").exists()
    assert (dirs / "output" / "v.diff.png").exists()


def test_small_noise_passes(dirs: Path) -> None:
    (dirs / "snapshots").mkdir()
    (dirs / "snapshots" / "v.png").write_bytes(png((100, 100, 100)))
    snapshot.assert_matches("v", png((100, 100, 100), dot=(110, 100, 100)))


def test_a_size_change_fails(dirs: Path) -> None:
    (dirs / "snapshots").mkdir()
    (dirs / "snapshots" / "v.png").write_bytes(png((0, 0, 0)))
    with pytest.raises(AssertionError, match="size"):
        snapshot.assert_matches("v", png((0, 0, 0), size=(41, 30)))


def test_a_missing_baseline_fails_and_lands_in_output(dirs: Path) -> None:
    with pytest.raises(AssertionError, match="did not exist"):
        snapshot.assert_matches("new", png((1, 2, 3)))
    assert (dirs / "snapshots" / "new.png").exists()
    assert (dirs / "output" / "new.actual.png").exists()


def test_update_rewrites_only_what_changed(dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDATE_SNAPSHOTS", "1")
    (dirs / "snapshots").mkdir()
    same = dirs / "snapshots" / "same.png"
    changed = dirs / "snapshots" / "changed.png"
    original = png((5, 5, 5))
    same.write_bytes(original)
    changed.write_bytes(original)
    reencoded = png((5, 5, 5), dot=(9, 5, 5))  # within tolerance, different bytes
    snapshot.assert_matches("same", reencoded)
    snapshot.assert_matches("changed", png((200, 5, 5)))
    assert same.read_bytes() == original
    assert changed.read_bytes() != original
