"""Compare a screenshot with its baseline in tests/visual/snapshots."""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image, ImageChops

SNAPSHOTS = Path(__file__).parent / "snapshots"
OUTPUT = Path(__file__).parent / "output"
# A pixel counts as changed when any one channel moves by more than this. It absorbs antialiasing noise.
CHANNEL_TOLERANCE = 16
# How many changed pixels a screenshot may have. The image is pinned, so renders repeat exactly; even a
# one-digit text change moves far more pixels than this.
MAX_CHANGED_PIXELS = 0


def updating() -> bool:
    return os.environ.get("UPDATE_SNAPSHOTS") == "1"


def difference(actual: Image.Image, expected: Image.Image) -> tuple[int, Image.Image]:
    """Changed pixel count and a mask of them. The strongest channel decides, so no color hides."""
    red, green, blue = ImageChops.difference(actual, expected).split()
    strongest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = strongest.point(lambda value: 255 if value > CHANNEL_TOLERANCE else 0)
    return mask.histogram()[255], mask


def compare(png: bytes, baseline: Path) -> tuple[str | None, Image.Image | None]:
    actual = Image.open(io.BytesIO(png)).convert("RGB")
    expected = Image.open(baseline).convert("RGB")
    if actual.size != expected.size:
        return f"size {actual.size} differs from the baseline {expected.size}", None
    changed, mask = difference(actual, expected)
    if changed > MAX_CHANGED_PIXELS:
        return f"{changed} pixels changed", mask
    return None, None


def assert_matches(name: str, png: bytes) -> None:
    """Fail when the screenshot differs from the baseline. Write actual and diff images to `OUTPUT`.

    With UPDATE_SNAPSHOTS=1 a differing or missing baseline is replaced instead, and a matching one
    is left alone, so git shows only the views that really changed.
    """
    baseline = SNAPSHOTS / f"{name}.png"
    problem, mask = compare(png, baseline) if baseline.exists() else ("did not exist and was created", None)
    if problem is None:
        return
    if updating() or not baseline.exists():
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(png)
        if updating():
            return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{name}.actual.png").write_bytes(png)
    if mask is not None:
        mask.save(OUTPUT / f"{name}.diff.png")
    raise AssertionError(f"{name}: {problem}. See tests/visual/output. UPDATE_SNAPSHOTS=1 accepts it.")
