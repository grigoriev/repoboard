"""Thin wrapper over the GitHub CLI. Every GitHub call of the app goes through `Gh`."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

Runner = Callable[[Sequence[str]], str]

TIMEOUT_SECONDS = 120
_HTTP_STATUS = re.compile(r"HTTP (\d{3})")


class GhError(RuntimeError):
    """A `gh` call failed. `status` holds the HTTP status when gh reports one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def server_error(self) -> bool:
        return self.status is not None and self.status >= 500


def run_gh(args: Sequence[str]) -> str:
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
    except FileNotFoundError as exc:
        raise GhError("the GitHub CLI 'gh' is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh {' '.join(args[:2])} timed out") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or f"gh exited with {result.returncode}"
        match = _HTTP_STATUS.search(message)
        raise GhError(message, int(match.group(1)) if match else None)
    return result.stdout


class Gh:
    def __init__(self, runner: Runner = run_gh) -> None:
        self._runner = runner

    def json(self, args: Sequence[str]) -> Any:
        output = self._runner(args)
        return json.loads(output) if output.strip() else None

    def paginate(self, endpoint: str) -> list[Any]:
        """Fetch every page of a REST list endpoint and return one flat list."""
        pages = self.json(["api", "--paginate", "--slurp", endpoint]) or []
        return [entry for page in pages for entry in page]

    def graphql(self, query: str, **variables: str) -> Any:
        args = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            args += ["-f", f"{name}={value}"]  # -F would turn a numeric repo name into an integer
        return self.json(args)

    def viewer_login(self) -> str:
        return str(self.json(["api", "user"])["login"])
