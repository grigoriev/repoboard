from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from repoboard.config import LANGUAGES
from repoboard.i18n import LANGUAGE_NAMES, Translator, catalog
from repoboard.models import Kind, Tag
from repoboard.triage import Section

PLACEHOLDER = re.compile(r"\{(\w+)\}")
NOW = datetime(2026, 10, 19, 9, tzinfo=UTC)


@pytest.mark.parametrize("language", LANGUAGES)
def test_catalogs_have_same_keys_and_placeholders(language: str) -> None:
    english = catalog("en")
    other = catalog(language)
    assert other.keys() == english.keys()
    for key, text in english.items():
        assert set(PLACEHOLDER.findall(other[key])) == set(PLACEHOLDER.findall(text)), key
        assert other[key].strip(), key


def test_every_enum_has_a_label() -> None:
    keys = catalog("en").keys()
    expected = (
        {f"tag.{t}" for t in Tag}
        | {f"kind.{k}" for k in Kind}
        | {f"group.{k}" for k in Kind}
        | {f"notify.new.{k}" for k in Kind if k is not Kind.ALERT}
        | {f"section.{s}" for s in Section}
    )
    assert expected <= keys
    assert set(LANGUAGE_NAMES) == set(LANGUAGES)


def test_translate_with_params_and_fallbacks() -> None:
    assert Translator("ru")("nav.overview") == "Обзор"
    assert Translator("de")("toolbar.repos", count=3) == "3 Repositories"
    assert Translator("fr").language == "en"
    assert Translator("en")("missing.key") == "missing.key"


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), "now"),
        (timedelta(minutes=5), "5 min"),
        (timedelta(hours=3, minutes=5), "3 h"),
        (timedelta(days=2, hours=1), "2 d"),
    ],
)
def test_age(delta: timedelta, expected: str) -> None:
    assert Translator().age(NOW - delta, NOW) == expected
