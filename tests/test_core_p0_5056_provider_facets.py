"""P0 #5056: provider-neutral, private local inspection facets."""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.core.library_local_inspection import (
    PROVIDERS, _provider, _provider_options,
)


def test_new_attested_provider_becomes_filter_without_core_allowlist():
    cases = [
        SimpleNamespace(provider="future-library"),
        SimpleNamespace(provider="bnf-pret"),
        SimpleNamespace(provider="future-library"),
        SimpleNamespace(provider="unattributed"),
    ]
    choices = _provider_options(cases)
    assert choices["all"] == PROVIDERS["all"]
    assert choices["bnf-pret"] == PROVIDERS["bnf-pret"]
    assert choices["future-library"] == "future-library"
    assert list(choices).count("future-library") == 1
    assert _provider("future-library") == "future-library"


def test_unknown_untrusted_identifier_cannot_become_a_filter_label():
    bad = ["", "PRIVATE_SECRET", "x" * 65, "secret/path", "other",
           "unattributed", "a\x00b", "provider..secret"]
    cases = [SimpleNamespace(provider=value) for value in bad]
    choices = _provider_options(cases)
    assert choices == PROVIDERS
    assert all(_provider(value) == "other" for value in bad)


def test_options_copy_does_not_mutate_global_labels():
    choices = _provider_options([SimpleNamespace(provider="new-provider")])
    choices["kobo"] = "changed"
    assert PROVIDERS["kobo"] == "Kobo"
    assert "new-provider" not in PROVIDERS
