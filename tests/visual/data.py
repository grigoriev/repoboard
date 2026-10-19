"""Fixed example data for the screenshots. Nothing here depends on the current time or on GitHub."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repoboard.models import Access, AlertSource, Item, Kind, RepoSnapshot, Tag
from repoboard.store import Store

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
REPOS = (
    "grigoriev/repoboard",
    "grigoriev/alfred-ssh-workflow",
    "intechcore/nginx-geoip2",
    "SchweizerischeBundesbahnen/ch.sbb.polarion.extension.pdf-exporter",
)
ALL_ACCESS = dict.fromkeys(AlertSource, Access.OK)


def ago(**delta: float) -> datetime:
    return NOW - timedelta(**delta)


def item(repo: str, kind: Kind, number: str, title: str, **fields: object) -> Item:
    created = fields.pop("created_at", ago(hours=3))
    path = {Kind.ISSUE: "issues", Kind.PR: "pull", Kind.DISCUSSION: "discussions", Kind.ALERT: "security"}[kind]
    values: dict[str, object] = {
        "repo": repo,
        "kind": kind,
        "number": number,
        "title": title,
        "url": f"https://github.com/{repo}/{path}/{number}",
        "author": "alice",
        "open": True,
        "created_at": created,
        "updated_at": created,
        "activity_at": created,
        "activity_by": "alice",
    }
    values.update(fields)
    return Item(**values)  # type: ignore[arg-type]


def snapshots() -> list[RepoSnapshot]:
    board, alfred, nginx, pdf = REPOS
    return [
        RepoSnapshot(
            board,
            (
                item(board, Kind.ISSUE, "12", "Overview should remember the sort order", created_at=ago(minutes=40)),
                item(
                    board,
                    Kind.ISSUE,
                    "4",
                    "Notifications do not open the issue",
                    open=False,
                    created_at=ago(days=20),
                    activity_at=ago(hours=2),
                    activity_by="bob",
                ),
                item(
                    board,
                    Kind.PR,
                    "15",
                    "chore: update dependency fastapi to v0.147.0",
                    author="app/renovate",
                    renovate=True,
                    activity_at=None,
                    activity_by=None,
                    tags=(Tag.REVIEW_REQUIRED, Tag.NO_AUTOMERGE),
                ),
                item(board, Kind.DISCUSSION, "3", "Support for GitLab?", created_at=ago(days=1, hours=2)),
            ),
            open_discussions=1,
            security=ALL_ACCESS,
        ),
        RepoSnapshot(
            alfred,
            (
                item(
                    alfred,
                    Kind.PR,
                    "88",
                    "chore: update actions/checkout digest",
                    author="app/renovate",
                    renovate=True,
                    activity_at=None,
                    activity_by=None,
                    tags=(Tag.CHECKS_FAILURE,),
                ),
                item(
                    alfred,
                    Kind.ALERT,
                    "code_scanning/9",
                    "Maintained",
                    author="Scorecard",
                    tags=(Tag.HIGH,),
                    created_at=ago(days=24),
                ),
            ),
            security=ALL_ACCESS,
        ),
        RepoSnapshot(
            nginx,
            (
                item(
                    nginx,
                    Kind.PR,
                    "41",
                    "feat: add the geoip2 city module",
                    author="carol",
                    tags=(Tag.CONFLICT,),
                    created_at=ago(days=2),
                ),
            ),
            security={AlertSource.DEPENDABOT: Access.NO_ACCESS, AlertSource.CODE_SCANNING: Access.NO_ACCESS},
        ),
        RepoSnapshot(
            pdf,
            (
                item(
                    pdf,
                    Kind.ALERT,
                    "dependabot/3",
                    "com.fasterxml.jackson.core:jackson-core: ReDoS in NumberInput.PATTERN_FLOAT",
                    author="dependabot",
                    tags=(Tag.CRITICAL,),
                    created_at=ago(days=14),
                ),
                item(
                    pdf, Kind.ISSUE, "10412", "Export fails for documents with nested tables", created_at=ago(minutes=5)
                ),
                item(
                    pdf,
                    Kind.ISSUE,
                    "390",
                    "Page numbers in the table of contents",
                    created_at=ago(days=60),
                    updated_at=ago(days=30),
                    activity_at=ago(days=60),
                ),
            ),
            security=ALL_ACCESS,
        ),
    ]


def watched() -> list[Item]:
    return [
        item(
            "cli/cli",
            Kind.PR,
            "12345",
            "feat: add gh repo watch",
            author="carol",
            open=False,
            tags=(Tag.MERGED,),
            created_at=ago(days=9),
            updated_at=ago(minutes=20),
            activity_at=ago(minutes=20),
            activity_by="carol",
        )
    ]


def seed(store: Store) -> None:
    store.set_setting("baseline", ago(days=7).isoformat())
    store.save_snapshots(snapshots(), NOW)
    store.save_watched(watched(), NOW)
    store.set_setting("last_poll", ago(minutes=2).isoformat())
    store.set_setting("data_version", "fixed")
