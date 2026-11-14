"""#5975: language preference ranking is headless and request-scoped.

Ranking is descriptive only. It must never resolve provider sessions, select
a new identity implicitly, or grant private source or acquisition authority.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.services import LibraryService


def _row(language, original=None):
    metadata = {"language": language}
    if original is not None:
        metadata["original_language"] = original
    return NS(metadata=metadata)


def test_core_language_score_is_pure_and_preserves_original_language_bonus():
    score = search_orchestration._search_preference_score
    assert score(_row("fr-FR", "fr"), reading_languages=("fr", "en"),
                 prefer_original_language=True) == 25
    assert score(_row("en-US"), reading_languages=("fr", "en")) == 10
    assert score(_row("de", "de"), reading_languages=("fr", "en"),
                 prefer_original_language=True) == 5
    assert score(_row("de"), reading_languages=("fr", "en")) == 0
    assert score(_row("fr"), reading_languages=()) == 0
    assert score(NS(metadata={"language": None}),
                 reading_languages=("fr",)) == 0


def test_facade_captures_one_profile_and_preferences_per_score():
    alice = NS(preferences=NS(reading_languages=["fr", "en"],
                              prefer_original_language=True))
    bob = NS(preferences=NS(reading_languages=["de"],
                            prefer_original_language=False))

    class Host:
        calls = 0

        @property
        def current_identity(self):
            self.calls += 1
            if self.calls == 1:
                return alice
            return bob

    host = Host()
    assert LibraryService._search_preference_score(
        host, _row("fr", "fr")
    ) == 25
    assert host.calls == 1
    assert LibraryService._search_preference_score(
        host, _row("fr", "fr")
    ) == 0
    assert host.calls == 2


def test_no_selection_fails_read_only_without_side_effects():
    class Host:
        @property
        def current_identity(self):
            return None

        def __getattr__(self, key):
            raise AssertionError(f"unexpected side effect: {key}")

    assert LibraryService._search_preference_score(Host(), _row("fr")) == 0


def test_core_language_worker_has_no_navigation_or_service_dependency():
    tree = ast.parse(inspect.getsource(search_orchestration._search_preference_score))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "current_identity"
        for node in ast.walk(tree)
    )
    assert "self" not in inspect.signature(
        search_orchestration._search_preference_score
    ).parameters
