"""Domain types: the items Repoboard shows and the snapshot of one repository."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Kind(StrEnum):
    ISSUE = "issue"
    DISCUSSION = "discussion"
    PR = "pr"
    ALERT = "alert"


class Tag(StrEnum):
    """Status marks on an item. The UI shows them as pills."""

    CHECKS_FAILURE = "checks_failure"
    CHECKS_PENDING = "checks_pending"
    CONFLICT = "conflict"
    MERGED = "merged"
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    NO_AUTOMERGE = "no_automerge"
    BLOCKED = "blocked"
    BEHIND = "behind"
    ANSWERED = "answered"
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SECRET = "secret"


class AlertSource(StrEnum):
    DEPENDABOT = "dependabot"
    CODE_SCANNING = "code_scanning"
    SECRET_SCANNING = "secret_scanning"


class Access(StrEnum):
    """Whether Repoboard could read a security alert source of a repository."""

    OK = "ok"
    NO_ACCESS = "no_access"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass(frozen=True)
class Item:
    repo: str
    kind: Kind
    number: str
    title: str
    url: str
    author: str
    open: bool
    created_at: datetime
    updated_at: datetime
    # The latest event by someone else: creation, comment or reply. None when only we or bots acted.
    activity_at: datetime | None
    activity_by: str | None = None
    tags: tuple[Tag, ...] = ()
    renovate: bool = False
    watched: bool = False  # listed under items in the config, so it has its own section

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.kind}#{self.number}"


@dataclass(frozen=True)
class RepoSnapshot:
    repo: str
    items: tuple[Item, ...] = ()
    open_discussions: int = 0
    security: dict[AlertSource, Access] = field(default_factory=dict)
    error: str | None = None
