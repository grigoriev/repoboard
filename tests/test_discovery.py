from __future__ import annotations

from typing import Any

import pytest

from repoboard.config import Config, Owner
from repoboard.discovery import RepoInfo, list_owner, matches, resolve
from repoboard.gh import Gh, GhError
from tests.fakes import FakeGhRunner


def repo(name: str, **flags: Any) -> dict[str, Any]:
    return {
        "full_name": name,
        "archived": False,
        "fork": False,
        "mirror_url": None,
        "is_template": False,
        "private": False,
        **flags,
    }


def test_repo_info_types() -> None:
    info = RepoInfo.from_api(repo("o/a", archived=True, fork=True, mirror_url="x", is_template=True))
    assert info.types == {"archived", "fork", "mirror", "template", "public"}
    assert RepoInfo.from_api(repo("o/b", private=True)).types == {"private"}


@pytest.mark.parametrize(
    ("owner", "name", "types", "expected"),
    [
        (Owner("o"), "a", {"public"}, True),
        (Owner("o"), "a", {"archived", "public"}, False),
        (Owner("o"), "a", {"fork", "public"}, False),
        (Owner("o", exclude_types=()), "a", {"archived", "public"}, True),
        (Owner("o", exclude_types=("private",)), "a", {"private"}, False),
        (Owner("o", include=("ch.sbb.*",)), "ch.sbb.x", {"public"}, True),
        (Owner("o", include=("ch.sbb.*",)), "other", {"public"}, False),
        (Owner("o", exclude=("articles",)), "Articles", {"public"}, False),
        (Owner("o", include=("*polarion*",), exclude=("*.st",)), "pdf-polarion.st", {"public"}, False),
    ],
)
def test_matches(owner: Owner, name: str, types: set[str], expected: bool) -> None:
    assert matches(owner, RepoInfo(f"o/{name}", frozenset(types))) is expected


def test_list_owner_reads_private_repos_of_the_viewer() -> None:
    runner = FakeGhRunner({"user/repos": [[repo("me/secret", private=True), repo("someone/else")]]})
    infos = list_owner(Gh(runner), "me", viewer="Me")
    assert [i.name for i in infos] == ["me/secret"]
    assert "affiliation=owner" in " ".join(runner.calls[0])


def test_list_owner_falls_back_to_the_user_listing() -> None:
    runner = FakeGhRunner({"orgs/alice/repos": GhError("Not Found", 404), "users/alice/repos": [[repo("alice/a")]]})
    assert [i.name for i in list_owner(Gh(runner), "alice", viewer="me")] == ["alice/a"]


def test_list_owner_reports_other_errors() -> None:
    gh = Gh(FakeGhRunner({"orgs/acme/repos": GhError("boom", 500)}))
    with pytest.raises(GhError):
        list_owner(gh, "acme", viewer="me")


def test_resolve_merges_owners_and_single_repos() -> None:
    runner = FakeGhRunner(
        {
            "orgs/acme/repos": [[repo("acme/b"), repo("acme/a"), repo("acme/old", archived=True)]],
            "orgs/broken/repos": GhError("boom", 500),
        }
    )
    cfg = Config(repos=("cli/cli", "acme/b"), owners=(Owner("acme"), Owner("broken")))
    result = resolve(Gh(runner), cfg, viewer="me")
    assert result.repos == ("acme/a", "acme/b", "cli/cli")
    assert result.errors == {"broken": "boom"}


def test_resolve_ignores_case_and_keeps_the_config_spelling() -> None:
    runner = FakeGhRunner({"orgs/acme/repos": [[repo("acme/tool")]]})
    cfg = Config(repos=("Acme/Tool",), owners=(Owner("acme"),))
    assert resolve(Gh(runner), cfg, viewer="me").repos == ("Acme/Tool",)
