"""Test doubles for the GitHub CLI."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from repoboard.gh import GhError

Route = Callable[[list[str]], Any]


class FakeGhRunner:
    """Answers `gh` calls from routes keyed by a substring of the joined arguments. First match wins."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        call = list(args)
        self.calls.append(call)
        joined = " ".join(call)
        for needle, answer in self.routes.items():
            if needle in joined:
                if isinstance(answer, GhError):
                    raise answer
                return json.dumps(answer(call) if callable(answer) else answer)
        raise AssertionError(f"unexpected gh call: {joined}")


def no_discussions() -> dict[str, Any]:
    return {"data": {"repository": {"hasDiscussionsEnabled": False}}}


def empty_repo_routes() -> dict[str, Any]:
    return {
        "issue list": [],
        "pr list": [],
        "api graphql": no_discussions(),
        "dependabot/alerts": [[]],
        "code-scanning/alerts": [[]],
        "secret-scanning/alerts": [[]],
    }
