from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from repoboard.models import Item, Kind, Tag
from repoboard.triage import Section, classify, marker

T1 = datetime(2026, 10, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 2, tzinfo=UTC)
T3 = datetime(2026, 10, 3, tzinfo=UTC)


def make(kind: Kind = Kind.ISSUE, **changes: object) -> Item:
    base = Item(
        repo="o/r",
        kind=kind,
        number="1",
        title="t",
        url="u",
        author="alice",
        open=True,
        created_at=T1,
        updated_at=T3,
        activity_at=T1,
        activity_by="alice",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("item", "section"),
    [
        (make(Kind.ALERT), Section.SECURITY),
        (make(), Section.NEW),
        (make(activity_at=T2, activity_by="bob"), Section.REPLIES),
        (make(activity_at=None), None),
        (make(Kind.DISCUSSION), Section.NEW),
        (make(Kind.PR, tags=(Tag.CHECKS_FAILURE,)), Section.FAILED),
        (make(Kind.PR, tags=(Tag.CONFLICT, Tag.DRAFT)), None),
        (make(Kind.PR, renovate=True, activity_at=None, tags=(Tag.NO_AUTOMERGE,)), Section.RENOVATE),
        (make(Kind.PR, renovate=True, activity_at=None, tags=(Tag.REVIEW_REQUIRED, Tag.CHECKS_PENDING)), None),
        (make(Kind.PR, renovate=True, activity_at=None, tags=()), None),
        (make(Kind.PR, renovate=True, activity_at=None, tags=(Tag.CHECKS_FAILURE,)), Section.FAILED),
        (make(Kind.PR), Section.NEW),
        (make(Kind.PR, activity_at=None), None),
    ],
)
def test_classify(item: Item, section: Section | None) -> None:
    assert classify(item) == section


def test_marker() -> None:
    assert marker(make(activity_at=T2), Section.REPLIES) == T2
    assert marker(make(Kind.PR), Section.FAILED) == T3
    assert marker(make(Kind.ALERT), Section.SECURITY) == T3
    assert marker(make(activity_at=None), Section.NEW) == T3
