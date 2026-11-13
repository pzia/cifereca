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
    assert choices["bnf-pret"] == "Bnf Pret"
    assert choices["future-library"] == "Future Library"
    assert list(choices).count("future-library") == 1
    assert choices["unattributed"] == PROVIDERS["unattributed"]
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
    choices["all"] = "changed"
    assert PROVIDERS["all"] == "Tous les fournisseurs"
    assert "new-provider" not in PROVIDERS


def test_builtin_provider_identifiers_are_not_predeclared_in_core():
    assert not {'kobo', 'numilog', 'paris', 'bnf-pret'} & PROVIDERS.keys()
    cases = [SimpleNamespace(provider=value) for value in
             ('kobo', 'numilog', 'paris', 'bnf-pret')]
    choices = _provider_options(cases)
    for value in ('kobo', 'numilog', 'paris', 'bnf-pret'):
        assert choices[value] == value.replace("-", " ").title()


def test_absent_well_formed_provider_filter_is_not_a_permission_error():
    # A legitimate but absent supplier is an empty read-only selection.
    # This keeps old bookmarked filters usable without a static supplier list.
    from cifereca.core.library_local_inspection import LocalInspection
    instance = object.__new__(LocalInspection)
    instance._valid = lambda: True
    instance._cases = {}
    instance._case_search = {}
    instance._limited = False
    assert instance.page(provider='future-library').cases == ()
    assert instance.page(provider='kobo').total == 0
    assert _provider('future-library') == 'future-library'
    for invalid in ('bad/path', 'SECRET', ''):
        assert _provider(invalid) == 'other'
        import pytest
        with pytest.raises(ValueError, match='invalid_inspection_page'):
            instance.page(provider=invalid)
