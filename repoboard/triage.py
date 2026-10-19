"""Decide which inbox section an item belongs to and when it last changed for us."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from repoboard.models import Item, Kind, Tag


class Section(StrEnum):
    """Inbox sections, in display order."""

    WATCHED = "watched"
    SECURITY = "security"
    FAILED = "failed"
    RENOVATE = "renovate"
    NEW = "new"
    REPLIES = "replies"


# State sections list an item while its state holds. Event sections list what happened after the baseline.
STATE_SECTIONS = frozenset({Section.WATCHED, Section.SECURITY, Section.FAILED, Section.RENOVATE})
_WAITING_TAGS = frozenset({Tag.REVIEW_REQUIRED, Tag.NO_AUTOMERGE, Tag.BLOCKED, Tag.BEHIND})
_BROKEN_TAGS = frozenset({Tag.CHECKS_FAILURE, Tag.CONFLICT})


def classify(item: Item) -> Section | None:
    if item.watched:
        return Section.WATCHED  # any change counts: a reply, a merge, a failed check
    if item.kind is Kind.ALERT:
        return Section.SECURITY
    tags = set(item.tags)
    if item.kind is Kind.PR:
        if Tag.DRAFT in tags:
            return None
        if tags & _BROKEN_TAGS:
            return Section.FAILED
        if item.renovate:
            return Section.RENOVATE if tags & _WAITING_TAGS and Tag.CHECKS_PENDING not in tags else None
    if item.activity_at is None:
        return None
    return Section.NEW if item.activity_at == item.created_at else Section.REPLIES


def marker(item: Item, section: Section) -> datetime:
    """The time that makes an item unseen again: its last update for state sections, else its last activity."""
    if section in STATE_SECTIONS or item.activity_at is None:
        return item.updated_at
    return item.activity_at
