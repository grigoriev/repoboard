"""Collect the items of one or many repositories through `gh`."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from repoboard.gh import Gh, GhError
from repoboard.models import Access, AlertSource, Item, Kind, RepoSnapshot, Tag

ISSUE_FIELDS = "number,title,url,author,state,createdAt,updatedAt"
PR_BASE_FIELDS = "number,title,url,author,createdAt,updatedAt,isDraft,reviewDecision,autoMergeRequest,mergeable"
# GitHub computes these per PR. In a busy repo one query with all of them times out, so they can go one by one.
PR_HEAVY_FIELDS = ("statusCheckRollup", "mergeStateStatus")
OPEN_LIMIT = "300"
RECENT_LIMIT = "100"
BOT_LOGINS = frozenset({"github-actions", "renovate", "dependabot", "coderabbitai", "sonarqubecloud", "codecov"})

# The newest reply of each of the last 20 threads: a reply can land in an older thread.
DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    hasDiscussionsEnabled
    open: discussions(states: OPEN) { totalCount }
    discussions(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number title url createdAt updatedAt closed isAnswered
        author { login }
        comments(last: 20) {
          nodes {
            createdAt author { login }
            replies(last: 1) { nodes { createdAt author { login } } }
          }
        }
      }
    }
  }
}
"""

_FAILED_CONCLUSIONS = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"})
_FAILED_STATES = frozenset({"FAILURE", "ERROR"})
_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
_SEVERITY_TAGS = {"critical": Tag.CRITICAL, "high": Tag.HIGH, "error": Tag.HIGH, "medium": Tag.MEDIUM}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_bot(login: str | None, flagged: bool = False) -> bool:
    if not login:
        return True
    name = login.removeprefix("app/").removesuffix("[bot]")
    return flagged or login.startswith("app/") or login.endswith("[bot]") or name in BOT_LOGINS


def _login(node: dict[str, Any] | None) -> str:
    return str((node or {}).get("login") or "ghost")


def latest_activity(events: Iterable[tuple[str, datetime]], me: str) -> tuple[datetime | None, str | None]:
    """Return the newest (time, login) among events by someone other than `me` and not by a bot."""
    foreign = [(at, login) for login, at in events if login != me and not is_bot(login)]
    if not foreign:
        return None, None
    at, login = max(foreign)
    return at, login


class Collector:
    def __init__(self, gh: Gh, me: str) -> None:
        self.gh = gh
        self.me = me

    def collect_all(
        self, repos: Sequence[str], since: datetime, workers: int = 8, on_done: Callable[[str], None] | None = None
    ) -> list[RepoSnapshot]:
        def one(repo: str) -> RepoSnapshot:
            snapshot = self.collect(repo, since)
            if on_done:
                on_done(repo)
            return snapshot

        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, repos))

    def collect(self, repo: str, since: datetime) -> RepoSnapshot:
        try:
            items = [*self.issues(repo, since), *self.pull_requests(repo)]
            discussions, open_discussions = self.discussions(repo)
            alerts, security = self.alerts(repo)
        except GhError as exc:
            return RepoSnapshot(repo=repo, error=str(exc))
        return RepoSnapshot(
            repo=repo,
            items=(*items, *discussions, *alerts),
            open_discussions=open_discussions,
            security=security,
        )

    def issues(self, repo: str, since: datetime) -> list[Item]:
        """Open issues, plus closed issues updated since `since`, with their comments."""
        open_issues = self.gh.json(
            ["issue", "list", "-R", repo, "--state", "open", "--limit", OPEN_LIMIT, "--json", ISSUE_FIELDS]
        )
        recent = self.gh.json(
            [
                "issue", "list", "-R", repo, "--state", "all", "--limit", RECENT_LIMIT,
                "--search", f"updated:>={since.date().isoformat()} sort:updated-desc",
                "--json", f"{ISSUE_FIELDS},comments",
            ]
        )  # fmt: skip
        by_number: dict[int, dict[str, Any]] = {node["number"]: node for node in open_issues}
        by_number.update({node["number"]: node for node in recent})
        return [self._issue(repo, node) for node in by_number.values()]

    def _issue(self, repo: str, node: dict[str, Any]) -> Item:
        author = _login(node.get("author"))
        created = parse_time(node["createdAt"])
        events = [(author, created)]
        events += [(_login(c.get("author")), parse_time(c["createdAt"])) for c in node.get("comments", [])]
        activity_at, activity_by = latest_activity(events, self.me)
        return Item(
            repo=repo,
            kind=Kind.ISSUE,
            number=str(node["number"]),
            title=node["title"],
            url=node["url"],
            author=author,
            open=node.get("state", "OPEN") == "OPEN",
            created_at=created,
            updated_at=parse_time(node["updatedAt"]),
            activity_at=activity_at,
            activity_by=activity_by,
        )

    def pull_requests(self, repo: str) -> list[Item]:
        nodes = self._pr_nodes(repo)
        return [self._pull_request(repo, node) for node in nodes]

    def _pr_nodes(self, repo: str) -> list[dict[str, Any]]:
        def query(fields: str) -> list[dict[str, Any]]:
            args = ["pr", "list", "-R", repo, "--state", "open", "--limit", OPEN_LIMIT, "--json", fields]
            return list(self.gh.json(args))

        try:
            return query(",".join([PR_BASE_FIELDS, *PR_HEAVY_FIELDS]))
        except GhError as exc:
            if not exc.server_error:
                raise
        by_number = {node["number"]: node for node in query(PR_BASE_FIELDS)}
        for heavy in PR_HEAVY_FIELDS:
            for node in query(f"number,{heavy}"):
                by_number.get(node["number"], {}).update(node)
        return list(by_number.values())

    def _pull_request(self, repo: str, node: dict[str, Any]) -> Item:
        author_node = node.get("author") or {}
        author = _login(author_node)
        created = parse_time(node["createdAt"])
        activity_at, activity_by = latest_activity([(author, created)], self.me)
        if is_bot(author, bool(author_node.get("is_bot"))):
            activity_at, activity_by = None, None
        return Item(
            repo=repo,
            kind=Kind.PR,
            number=str(node["number"]),
            title=node["title"],
            url=node["url"],
            author=author,
            open=True,
            created_at=created,
            updated_at=parse_time(node["updatedAt"]),
            activity_at=activity_at,
            activity_by=activity_by,
            tags=pr_tags(node),
            renovate="renovate" in author,
        )

    def discussions(self, repo: str) -> tuple[list[Item], int]:
        owner, name = repo.split("/", 1)
        data = self.gh.graphql(DISCUSSIONS_QUERY, owner=owner, name=name)["data"]["repository"]
        if not data["hasDiscussionsEnabled"]:
            return [], 0
        return [self._discussion(repo, node) for node in data["discussions"]["nodes"]], data["open"]["totalCount"]

    def _discussion(self, repo: str, node: dict[str, Any]) -> Item:
        author = _login(node.get("author"))
        created = parse_time(node["createdAt"])
        events = [(author, created)]
        for comment in node["comments"]["nodes"]:
            events.append((_login(comment.get("author")), parse_time(comment["createdAt"])))
            events += [(_login(r.get("author")), parse_time(r["createdAt"])) for r in comment["replies"]["nodes"]]
        activity_at, activity_by = latest_activity(events, self.me)
        return Item(
            repo=repo,
            kind=Kind.DISCUSSION,
            number=str(node["number"]),
            title=node["title"],
            url=node["url"],
            author=author,
            open=not node["closed"],
            created_at=created,
            updated_at=parse_time(node["updatedAt"]),
            activity_at=activity_at,
            activity_by=activity_by,
            tags=(Tag.ANSWERED,) if node.get("isAnswered") else (),
        )

    def alerts(self, repo: str) -> tuple[list[Item], dict[AlertSource, Access]]:
        items: list[Item] = []
        access: dict[AlertSource, Access] = {}
        for source, path in (
            (AlertSource.DEPENDABOT, "dependabot/alerts"),
            (AlertSource.CODE_SCANNING, "code-scanning/alerts"),
            (AlertSource.SECRET_SCANNING, "secret-scanning/alerts"),
        ):
            try:
                nodes = self.gh.paginate(f"repos/{repo}/{path}?state=open&per_page=100")
            except GhError as exc:
                access[source] = {403: Access.NO_ACCESS, 404: Access.DISABLED}.get(exc.status or 0, Access.ERROR)
                continue
            access[source] = Access.OK
            items += [alert_item(repo, source, node) for node in nodes]
        return items, access


def pr_tags(node: dict[str, Any]) -> tuple[Tag, ...]:
    tags: list[Tag] = []
    checks = check_state(node.get("statusCheckRollup") or [])
    if checks:
        tags.append(checks)
    if node.get("mergeable") == "CONFLICTING":
        tags.append(Tag.CONFLICT)
    if node.get("isDraft"):
        tags.append(Tag.DRAFT)
    if node.get("reviewDecision") == "REVIEW_REQUIRED":
        tags.append(Tag.REVIEW_REQUIRED)
    if node.get("autoMergeRequest") is None:
        tags.append(Tag.NO_AUTOMERGE)
    merge_state = node.get("mergeStateStatus")
    if merge_state == "BLOCKED":
        tags.append(Tag.BLOCKED)
    elif merge_state == "BEHIND":
        tags.append(Tag.BEHIND)
    return tuple(tags)


def check_state(rollup: list[dict[str, Any]]) -> Tag | None:
    """Failure if any check failed, pending if any still runs, None when all passed or there are none."""
    pending = False
    for check in rollup:
        if check.get("__typename") == "StatusContext":
            state = check.get("state")
            if state in _FAILED_STATES:
                return Tag.CHECKS_FAILURE
            pending = pending or state in _PENDING_STATES
        elif check.get("status") != "COMPLETED":
            pending = True
        elif check.get("conclusion") in _FAILED_CONCLUSIONS:
            return Tag.CHECKS_FAILURE
    return Tag.CHECKS_PENDING if pending else None


AlertFields = tuple[str, str | None, str]  # title, severity, author


def _dependabot_fields(node: dict[str, Any]) -> AlertFields:
    advisory = node.get("security_advisory") or {}
    package = ((node.get("dependency") or {}).get("package") or {}).get("name", "")
    title = f"{package}: {advisory.get('summary', '')}".strip(": ")
    severity = (node.get("security_vulnerability") or {}).get("severity") or advisory.get("severity")
    return title, severity, "dependabot"


def _code_scanning_fields(node: dict[str, Any]) -> AlertFields:
    rule = node.get("rule") or {}
    title = rule.get("description") or rule.get("name") or ""
    severity = rule.get("security_severity_level") or rule.get("severity")
    return title, severity, (node.get("tool") or {}).get("name") or "code scanning"


def _secret_scanning_fields(node: dict[str, Any]) -> AlertFields:
    return node.get("secret_type_display_name") or node.get("secret_type") or "", "secret", "secret scanning"


_ALERT_FIELDS = {
    AlertSource.DEPENDABOT: _dependabot_fields,
    AlertSource.CODE_SCANNING: _code_scanning_fields,
    AlertSource.SECRET_SCANNING: _secret_scanning_fields,
}


def alert_item(repo: str, source: AlertSource, node: dict[str, Any]) -> Item:
    created = parse_time(node["created_at"])
    title, severity, author = _ALERT_FIELDS[source](node)
    tag = Tag.SECRET if severity == "secret" else _SEVERITY_TAGS.get(str(severity).lower(), Tag.LOW)
    return Item(
        repo=repo,
        kind=Kind.ALERT,
        number=f"{source}/{node['number']}",
        title=title,
        url=node["html_url"],
        author=author,
        open=True,
        created_at=created,
        updated_at=parse_time(node.get("updated_at") or node["created_at"]),
        activity_at=created,
        activity_by=author,
        tags=(tag,),
    )
