"""Compare a screenshot with its baseline in tests/visual/snapshots."""

from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image, ImageChops

SNAPSHOTS = Path(__file__).parent / "snapshots"
OUTPUT = Path(__file__).parent / "output"
# A pixel counts as changed when a channel moves by more than this. It absorbs antialiasing noise.
CHANNEL_TOLERANCE = 16
# The share of changed pixels a screenshot may have before the test fails.
MAX_CHANGED_RATIO = 0.001


def updating() -> bool:
    return os.environ.get("UPDATE_SNAPSHOTS") == "1"


def assert_matches(name: str, png: bytes) -> None:
    """Fail when the screenshot differs from the baseline. Write actual and diff images next to it."""
    baseline = SNAPSHOTS / f"{name}.png"
    if updating() or not baseline.exists():
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(png)
        if not updating():
            raise AssertionError(f"{baseline.name} did not exist and was created. Review it and commit it.")
        return
    actual = Image.open(io.BytesIO(png)).convert("RGB")
    expected = Image.open(baseline).convert("RGB")
    problem = None
    if actual.size != expected.size:
        problem = f"size {actual.size} differs from the baseline {expected.size}"
    else:
        diff = ImageChops.difference(actual, expected)
        mask = diff.convert("L").point(lambda value: 255 if value > CHANNEL_TOLERANCE else 0)
        changed = mask.histogram()[255]
        ratio = changed / (actual.width * actual.height)
        if ratio > MAX_CHANGED_RATIO:
            problem = f"{changed} pixels changed ({ratio:.3%})"
            OUTPUT.mkdir(parents=True, exist_ok=True)
            mask.save(OUTPUT / f"{name}.diff.png")
    if problem:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / f"{name}.actual.png").write_bytes(png)
        raise AssertionError(f"{name}: {problem}. See tests/visual/output. UPDATE_SNAPSHOTS=1 accepts it.")
