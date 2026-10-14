"""#1599: malformed provider language fields must not abort Recommendations.

Only explicitly attested string tags are projected; no network or GTK.
"""
from __future__ import annotations

import pytest

from recommendations import _candidate_languages


class Untrusted:
    __hash__ = None

    def __str__(self):
        raise AssertionError("Never stringify a non-language catalogue value")


def test_nested_language_metadata_is_ignored_without_str_or_typeerror():
    row = {
        "language": {"tag": "fr"},
        "languages": ["fr", {"tag": "es"}, 42, False, None, Untrusted(), "en"],
        "metadata": {
            "language": Untrusted(),
            "languages": ["de", "fr", {"tag": "it"}, Untrusted()],
        },
    }
    assert _candidate_languages(row) == ("fr", "en", "de")


@pytest.mark.parametrize("bad", [
    {}, {"tag": "en"}, 9, False, None, Untrusted(),
    [["en"]], ({"language": "de"},),
])
def test_malformed_scalar_is_not_a_language_tag(bad):
    assert _candidate_languages({"language": bad}) == ()
    assert _candidate_languages({"metadata": {"languages": bad}}) == ()


def test_duplicate_and_invalid_strings_preserve_existing_normalization():
    row = {
        "languages": ["fre", "fr", "invalid language", "en"],
        "metadata": {"language": "en", "languages": ["de", "fre"]},
    }
    assert _candidate_languages(row) == ("fr", "en", "de")
