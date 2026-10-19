"""Command line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from repoboard import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repoboard", description="Daily overview of your GitHub repositories.")
    parser.add_argument("--version", action="version", version=f"repoboard {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
