from __future__ import annotations

import pytest

from repoboard import __version__
from repoboard.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_arguments() -> None:
    assert main([]) == 0
