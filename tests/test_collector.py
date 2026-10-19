from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from repoboard.collector import Collector, check_state, is_bot, latest_activity, parse_time, pr_tags
from repoboard.gh import Gh, GhError
from repoboard.models import Access, AlertSource, Kind, Tag
from tests.fakes import FakeGhRunner, empty_repo_routes

SINCE = datetime(2026, 10, 1, tzinfo=UTC)


def collector(routes: dict[str, Any]) -> tuple[Collector, FakeGhRunner]:
    runner = FakeGhRunner(routes)
    return Collector(Gh(runner), me="me"), runner


def issue(number: int, author: str = "alice", state: str = "OPEN", **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "author": {"login": author},
        "state": state,
        "createdAt": "2026-09-01T10:00:00Z",
        "updatedAt": "2026-10-02T10:00:00Z",
        **extra,
    }


def comment(author: str, at: str) -> dict[str, Any]:
    return {"author": {"login": author}, "createdAt": at}


def test_parse_time() -> None:
    assert parse_time("2026-10-02T10:00:00Z") == datetime(2026, 10, 2, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("login", "flagged", "expected"),
    [
        ("alice", False, False),
        ("alice", True, True),
        (None, False, True),
        ("app/renovate", False, True),
        ("some[bot]", False, True),
        ("github-actions", False, True),
    ],
)
def test_is_bot(login: str | None, flagged: bool, expected: bool) -> None:
    assert is_bot(login, flagged) is expected


def test_latest_activity_ignores_me_and_bots() -> None:
    t1, t2, t3 = (datetime(2026, 10, d, tzinfo=UTC) for d in (1, 2, 3))
    assert latest_activity([("alice", t1), ("bob", t2), ("me", t3), ("renovate[bot]", t3)], "me") == (t2, "bob")
    assert latest_activity([("me", t1)], "me") == (None, None)


def test_issues_merge_open_and_recent_with_comments() -> None:
    def issues(args: list[str]) -> list[dict[str, Any]]:
        if "open" in args:
            return [issue(1), issue(2, author="me")]
        assert "updated:>=2026-10-01" in args
        closed = issue(3, author="me", state="CLOSED", comments=[comment("bob", "2026-10-03T08:00:00Z")])
        return [issue(2, author="me", comments=[comment("me", "2026-10-02T09:00:00Z")]), closed]

    c, _ = collector({**empty_repo_routes(), "issue list": issues})
    snapshot = c.collect("o/r", SINCE)
    by_number = {i.number: i for i in snapshot.items}
    assert set(by_number) == {"1", "2", "3"}
    assert by_number["1"].activity_by == "alice"
    assert by_number["2"].activity_at is None
    assert by_number["3"].open is False
    assert by_number["3"].activity_by == "bob"
    assert by_number["3"].key == "o/r#issue#3"


def pr(number: int, author: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/o/r/pull/{number}",
        "author": author,
        "createdAt": "2026-10-02T10:00:00Z",
        "updatedAt": "2026-10-03T10:00:00Z",
        "isDraft": False,
        "reviewDecision": "",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "statusCheckRollup": [],
        "autoMergeRequest": {"mergeMethod": "SQUASH"},
        **extra,
    }


def test_pull_requests() -> None:
    nodes = [
        pr(5, {"login": "app/renovate", "is_bot": True}, autoMergeRequest=None, reviewDecision="REVIEW_REQUIRED"),
        pr(6, {"login": "carol", "is_bot": False}, mergeable="CONFLICTING", isDraft=True),
    ]
    c, _ = collector({**empty_repo_routes(), "pr list": nodes})
    items = {i.number: i for i in c.collect("o/r", SINCE).items}
    assert items["5"].renovate is True
    assert items["5"].activity_at is None
    assert items["5"].tags == (Tag.REVIEW_REQUIRED, Tag.NO_AUTOMERGE)
    assert items["6"].renovate is False
    assert items["6"].activity_by == "carol"
    assert items["6"].tags == (Tag.CONFLICT, Tag.DRAFT)


@pytest.mark.parametrize(
    ("rollup", "expected"),
    [
        ([], None),
        ([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SUCCESS"}], None),
        ([{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "SKIPPED"}], None),
        ([{"__typename": "CheckRun", "status": "IN_PROGRESS", "conclusion": ""}], Tag.CHECKS_PENDING),
        (
            [
                {"__typename": "CheckRun", "status": "IN_PROGRESS"},
                {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"},
            ],
            Tag.CHECKS_FAILURE,
        ),
        ([{"__typename": "StatusContext", "state": "PENDING"}], Tag.CHECKS_PENDING),
        ([{"__typename": "StatusContext", "state": "ERROR"}], Tag.CHECKS_FAILURE),
        ([{"__typename": "StatusContext", "state": "SUCCESS"}], None),
    ],
)
def test_check_state(rollup: list[dict[str, Any]], expected: Tag | None) -> None:
    assert check_state(rollup) == expected


@pytest.mark.parametrize(("state", "tag"), [("BLOCKED", Tag.BLOCKED), ("BEHIND", Tag.BEHIND), ("DIRTY", None)])
def test_pr_tags_merge_state(state: str, tag: Tag | None) -> None:
    tags = pr_tags({"mergeStateStatus": state, "autoMergeRequest": {}})
    assert tags == ((tag,) if tag else ())


def test_discussions() -> None:
    data = {
        "data": {
            "repository": {
                "hasDiscussionsEnabled": True,
                "open": {"totalCount": 7},
                "discussions": {
                    "nodes": [
                        {
                            "number": 17,
                            "title": "Question",
                            "url": "https://github.com/o/r/discussions/17",
                            "createdAt": "2026-09-01T10:00:00Z",
                            "updatedAt": "2026-10-04T10:00:00Z",
                            "closed": True,
                            "isAnswered": True,
                            "author": {"login": "me"},
                            "comments": {
                                "nodes": [
                                    {
                                        **comment("me", "2026-10-03T10:00:00Z"),
                                        "replies": {"nodes": [comment("dave", "2026-10-04T10:00:00Z")]},
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        }
    }
    c, runner = collector({**empty_repo_routes(), "api graphql": data})
    snapshot = c.collect("o/r", SINCE)
    (item,) = snapshot.items
    assert snapshot.open_discussions == 7
    assert item.kind is Kind.DISCUSSION
    assert item.open is False
    assert item.tags == (Tag.ANSWERED,)
    assert item.activity_by == "dave"
    assert "owner=o" in runner.calls[-4] and "name=r" in runner.calls[-4]


def test_alerts_and_access() -> None:
    dependabot = {
        "number": 3,
        "html_url": "https://github.com/o/r/security/dependabot/3",
        "created_at": "2026-10-05T10:00:00Z",
        "updated_at": "2026-10-05T11:00:00Z",
        "dependency": {"package": {"name": "jackson"}},
        "security_advisory": {"summary": "RCE", "severity": "high"},
        "security_vulnerability": {"severity": "critical"},
    }
    routes = {
        **empty_repo_routes(),
        "dependabot/alerts": [[dependabot]],
        "code-scanning/alerts": GhError("forbidden", 403),
        "secret-scanning/alerts": GhError("not found", 404),
    }
    c, _ = collector(routes)
    snapshot = c.collect("o/r", SINCE)
    (item,) = snapshot.items
    assert item.number == "dependabot/3"
    assert item.title == "jackson: RCE"
    assert item.tags == (Tag.CRITICAL,)
    assert snapshot.security == {
        AlertSource.DEPENDABOT: Access.OK,
        AlertSource.CODE_SCANNING: Access.NO_ACCESS,
        AlertSource.SECRET_SCANNING: Access.DISABLED,
    }


def test_code_and_secret_alerts() -> None:
    code = {
        "number": 9,
        "html_url": "https://github.com/o/r/security/code-scanning/9",
        "created_at": "2026-10-05T10:00:00Z",
        "rule": {"description": "Maintained", "severity": "error"},
        "tool": {"name": "Scorecard"},
    }
    secret = {
        "number": 1,
        "html_url": "https://github.com/o/r/security/secret-scanning/1",
        "created_at": "2026-10-05T10:00:00Z",
        "secret_type_display_name": "GitHub Token",
    }
    routes = {
        **empty_repo_routes(),
        "dependabot/alerts": GhError("boom"),
        "code-scanning/alerts": [[code]],
        "secret-scanning/alerts": [[secret]],
    }
    c, _ = collector(routes)
    snapshot = c.collect("o/r", SINCE)
    items = {i.number: i for i in snapshot.items}
    assert items["code_scanning/9"].tags == (Tag.HIGH,)
    assert items["code_scanning/9"].author == "Scorecard"
    assert items["secret_scanning/1"].tags == (Tag.SECRET,)
    assert items["secret_scanning/1"].title == "GitHub Token"
    assert snapshot.security[AlertSource.DEPENDABOT] is Access.ERROR


def test_collect_reports_repo_error() -> None:
    c, _ = collector({"issue list": GhError("Could not resolve to a Repository")})
    snapshot = c.collect("o/gone", SINCE)
    assert snapshot.error == "Could not resolve to a Repository"
    assert snapshot.items == ()


def test_collect_all_runs_every_repo() -> None:
    c, _ = collector(empty_repo_routes())
    done: list[str] = []
    snapshots = c.collect_all(["o/a", "o/b"], SINCE, workers=2, on_done=done.append)
    assert [s.repo for s in snapshots] == ["o/a", "o/b"]
    assert sorted(done) == ["o/a", "o/b"]


def test_pull_requests_split_query_on_server_error() -> None:
    def prs(args: list[str]) -> list[dict[str, Any]]:
        fields = args[-1]
        if "statusCheckRollup" in fields and "title" in fields:
            raise GhError("HTTP 502: Bad Gateway", 502)
        if fields == "number,statusCheckRollup":
            failed = {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}
            return [{"number": 6, "statusCheckRollup": [failed]}, {"number": 99, "statusCheckRollup": []}]
        if fields == "number,mergeStateStatus":
            return [{"number": 6, "mergeStateStatus": "BEHIND"}]
        return [pr(6, {"login": "carol"}, mergeStateStatus=None)]

    c, runner = collector({**empty_repo_routes(), "pr list": prs})
    (item,) = c.pull_requests("o/r")
    assert item.tags == (Tag.CHECKS_FAILURE, Tag.BEHIND)
    assert len([call for call in runner.calls if "pr" in call]) == 4


def test_pull_requests_client_error_propagates() -> None:
    c, _ = collector({**empty_repo_routes(), "pr list": GhError("HTTP 404", 404)})
    with pytest.raises(GhError):
        c.pull_requests("o/r")
