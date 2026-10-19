"""Turn the owners of the config into repositories: list them through gh and filter them."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from repoboard.config import Config, Owner
from repoboard.gh import Gh, GhError


@dataclass(frozen=True)
class RepoInfo:
    name: str  # owner/name
    types: frozenset[str]

    @classmethod
    def from_api(cls, node: dict[str, Any]) -> RepoInfo:
        flags = {
            "archived": bool(node.get("archived")),
            "fork": bool(node.get("fork")),
            "mirror": bool(node.get("mirror_url")),
            "template": bool(node.get("is_template")),
            "private": bool(node.get("private")),
            "public": not node.get("private"),
        }
        return cls(str(node["full_name"]), frozenset(kind for kind, on in flags.items() if on))


@dataclass(frozen=True)
class Resolved:
    repos: tuple[str, ...]
    errors: dict[str, str] = field(default_factory=dict)  # owner -> why it could not be listed


def matches(owner: Owner, info: RepoInfo) -> bool:
    if info.types & set(owner.exclude_types):
        return False
    name = info.name.split("/", 1)[1].lower()

    def any_of(patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns)

    return (not owner.include or any_of(owner.include)) and not any_of(owner.exclude)


def list_owner(gh: Gh, owner: str, viewer: str) -> list[RepoInfo]:
    """Every repository of a user or organization. REST, so it spares the GraphQL quota of the polls."""
    if owner.lower() == viewer.lower():
        # Only this listing shows the private repositories of the signed in user.
        nodes = gh.paginate("user/repos?affiliation=owner&visibility=all&per_page=100")
        return [RepoInfo.from_api(n) for n in nodes if str(n["full_name"]).lower().startswith(f"{owner.lower()}/")]
    try:
        nodes = gh.paginate(f"orgs/{owner}/repos?type=all&per_page=100")
    except GhError as exc:
        if exc.status != 404:
            raise
        nodes = gh.paginate(f"users/{owner}/repos?type=owner&per_page=100")
    return [RepoInfo.from_api(node) for node in nodes]


def resolve(gh: Gh, config: Config, viewer: str) -> Resolved:
    """The repositories to poll: the matching repositories of every owner, plus the single ones."""
    repos = set(config.repos)
    errors: dict[str, str] = {}
    for owner in config.owners:
        try:
            repos.update(info.name for info in list_owner(gh, owner.name, viewer) if matches(owner, info))
        except GhError as exc:
            errors[owner.name] = str(exc)
    return Resolved(tuple(sorted(repos, key=str.lower)), errors)
