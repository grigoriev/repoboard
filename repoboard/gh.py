"""Thin wrapper over the GitHub CLI. Every GitHub call of the app goes through `Gh`."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

Runner = Callable[[Sequence[str]], str]

TIMEOUT_SECONDS = 120
DOWNLOAD_TIMEOUT_SECONDS = 600  # the app zip is tens of MB
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
    timeout = DOWNLOAD_TIMEOUT_SECONDS if list(args[:2]) == ["release", "download"] else TIMEOUT_SECONDS
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout, check=False)
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

    def graphql(self, query: str, **variables: str | int) -> Any:
        args = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            # -f keeps a numeric repo name a string; -F sends a real integer, as Int! variables need.
            args += ["-F" if isinstance(value, int) else "-f", f"{name}={value}"]
        return self.json(args)

    def viewer_login(self) -> str:
        return str(self.json(["api", "user"])["login"])

    def latest_release(self, repo: str) -> Any:
        return self.json(["api", f"repos/{repo}/releases/latest"])

    def download_asset(self, repo: str, tag: str, name: str, directory: str) -> None:
        self._runner(["release", "download", tag, "--repo", repo, "--pattern", name, "--dir", directory])

    def verify_attestation(self, path: str, repo: str) -> None:
        """Fail unless the file carries a build provenance attestation of the repository."""
        self._runner(["attestation", "verify", path, "--repo", repo])
