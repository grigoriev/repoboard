from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from repoboard import gh
from repoboard.gh import Gh, GhError


class FakeRunner:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        return self.output


def test_json_and_empty_output() -> None:
    assert Gh(FakeRunner('{"a": 1}')).json(["x"]) == {"a": 1}
    assert Gh(FakeRunner("  \n")).json(["x"]) is None


def test_paginate_flattens_pages() -> None:
    runner = FakeRunner("[[1, 2], [3]]")
    assert Gh(runner).paginate("repos/a/b/x") == [1, 2, 3]
    assert runner.calls == [["api", "--paginate", "--slurp", "repos/a/b/x"]]


def test_graphql_passes_variables() -> None:
    runner = FakeRunner('{"data": {}}')
    Gh(runner).graphql("query{}", owner="a", name="2048")
    # -f keeps a numeric repo name a string; -F would send it as an integer.
    assert runner.calls == [["api", "graphql", "-f", "query=query{}", "-f", "owner=a", "-f", "name=2048"]]


def test_viewer_login() -> None:
    assert Gh(FakeRunner('{"login": "me"}')).viewer_login() == "me"


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["gh"], returncode, stdout, stderr)


def test_run_gh_success(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        return _completed(0, "out")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gh.run_gh(["api", "user"]) == "out"
    assert seen["cmd"] == ["gh", "api", "user"]


@pytest.mark.parametrize(
    ("stderr", "status"),
    [
        ("gh: Not Found (HTTP 404)", 404),
        ("HTTP 502: 502 Bad Gateway (https://api.github.com)", 502),
        ("boom", None),
        ("", None),
    ],
)
def test_run_gh_failure(monkeypatch: pytest.MonkeyPatch, stderr: str, status: int | None) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed(1, stderr=stderr))
    with pytest.raises(GhError) as exc:
        gh.run_gh(["api", "x"])
    assert exc.value.status == status


@pytest.mark.parametrize(
    ("error", "text"),
    [(FileNotFoundError(), "not installed"), (subprocess.TimeoutExpired("gh", 1), "timed out")],
)
def test_run_gh_os_errors(monkeypatch: pytest.MonkeyPatch, error: Exception, text: str) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise error

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GhError, match=text):
        gh.run_gh(["api", "x"])


def test_server_error() -> None:
    assert GhError("x", 502).server_error
    assert not GhError("x", 404).server_error
    assert not GhError("x").server_error


def test_graphql_passes_integers_typed() -> None:
    runner = FakeRunner('{"data": {}}')
    Gh(runner).graphql("q", name="2048", number=3)
    assert runner.calls == [["api", "graphql", "-f", "query=q", "-f", "name=2048", "-F", "number=3"]]
