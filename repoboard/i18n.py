"""Translations. English is the default; every catalog in `locales/` has the same keys."""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from importlib import resources

from repoboard.config import LANGUAGES

DEFAULT_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "ru": "Русский", "de": "Deutsch"}


@cache
def catalog(language: str) -> dict[str, str]:
    text = resources.files("repoboard").joinpath("locales", f"{language}.json").read_text(encoding="utf-8")
    return dict(json.loads(text))


class Translator:
    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = language if language in LANGUAGES else DEFAULT_LANGUAGE
        self._messages = catalog(self.language)
        self._fallback = catalog(DEFAULT_LANGUAGE)

    def __call__(self, key: str, **params: object) -> str:
        template = self._messages.get(key) or self._fallback.get(key) or key
        return template.format(**params) if params else template

    def age(self, then: datetime, now: datetime) -> str:
        """Short relative age: 'now', '5 min', '3 h', '2 d'."""
        minutes = int((now - then).total_seconds() // 60)
        if minutes < 1:
            return self("age.now")
        if minutes < 60:
            return self("age.minutes", count=minutes)
        if minutes < 24 * 60:
            return self("age.hours", count=minutes // 60)
        return self("age.days", count=minutes // (24 * 60))
