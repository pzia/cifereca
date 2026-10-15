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


class HostileLanguage(str):
    def strip(self, *_args, **_kwargs):
        raise AssertionError("Bearer PRIVATE_LANGUAGE")


class UnreadableMapping(dict):
    def get(self, name, default=None):
        if name == "language":
            raise RuntimeError("Bearer PRIVATE_ACCOUNT")
        return super().get(name, default)


def test_poisoned_metadata_accessor_does_not_hide_independent_attested_tags():
    row = {
        "language": "fre",
        "languages": ["en", HostileLanguage("PRIVATE")],
        "metadata": UnreadableMapping({"language": "it", "languages": ["de"]}),
    }
    assert _candidate_languages(row) == ("fr", "en", "de")


def test_failed_normalizer_quarantines_only_its_tag(monkeypatch):
    import recommendations

    normalizer = recommendations.normalize_language_tag

    def poisoned(value):
        if value == "unsafe":
            raise RuntimeError("Bearer PRIVATE_LANGUAGE")
        if value == "unexpected":
            return {"language": "PRIVATE"}  # malformed policy return
        return normalizer(value)

    monkeypatch.setattr(recommendations, "normalize_language_tag", poisoned)
    row = {"languages": ["fr", "unsafe", "en", "unexpected", "de"]}
    assert _candidate_languages(row) == ("fr", "en", "de")


def test_language_array_scan_is_bounded_and_does_not_process_late_poison(monkeypatch):
    import recommendations

    inspected = []
    original = recommendations.normalize_language_tag

    def count(value):
        inspected.append(value)
        if value == "PRIVATE_LAST":
            raise AssertionError("should not inspect unbounded metadata")
        return original(value)

    monkeypatch.setattr(recommendations, "normalize_language_tag", count)
    languages = ["fr"] * 1024 + ["PRIVATE_LAST"]
    assert _candidate_languages({"languages": languages}) == ("fr",)
    assert len(inspected) == 128


def test_invalid_root_mapping_getter_is_not_allowed_to_abort_projection():
    class Root(dict):
        def get(self, key, default=None):
            if key in {"language", "languages"}:
                raise RuntimeError("PRIVATE_SOURCE")
            return super().get(key, default)

    assert _candidate_languages(Root(metadata={"languages": ["en", "fr"]})) == ("en", "fr")
