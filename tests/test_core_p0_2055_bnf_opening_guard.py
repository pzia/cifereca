"""P0 #2055: opening Cifereca cannot acquire a BnF Web loan implicitly.

These are synthetic loan DTOs. No BnF account, browser, ACSM, HTTP or file IO.
The separate explicitly confirmed Web download flow is intentionally out of
scope here. The source's presence in a loan inventory is not user consent.
"""
from __future__ import annotations

import pytest

from cifereca.models import Loan
from cifereca.core.sync_coordination import _opening_loan_acquisition_allowed


def _loan(provider="bnf-pret", *, medium="digital", ebook_url=None, raw=None):
    return Loan(
        id="fictional-loan", provider="SYRACUSE", medium=medium,
        record_id="fictional-resource", title="Synthetic book",
        source_provider=provider, source_credential="fictional-credential",
        source_account_id="fictional-account", identity_id="fictional-identity",
        ebook_url=ebook_url, raw=raw or {},
    )


@pytest.mark.parametrize("medium", ["digital", "physical", "unknown"])
@pytest.mark.parametrize("has_url,has_metadata", [(False, False), (True, False), (False, True), (True, True)])
def test_bnf_observation_never_authorizes_opening_download(medium, has_url, has_metadata):
    row = _loan(medium=medium,
                ebook_url="https://example.invalid/fictional" if has_url else None,
                raw={"fixture": True} if has_metadata else {})
    assert _opening_loan_acquisition_allowed(row) is False


@pytest.mark.parametrize("provider", ["paris", "kobo", "numilog"])
def test_existing_provider_auto_acquisition_rules_are_unchanged(provider):
    assert _opening_loan_acquisition_allowed(_loan(provider, ebook_url="fictional")) is True
    assert _opening_loan_acquisition_allowed(_loan(provider, raw={"fixture": True})) is True
    assert _opening_loan_acquisition_allowed(_loan(provider, medium="physical", ebook_url="fictional")) is False
    assert _opening_loan_acquisition_allowed(_loan(provider)) is False


def test_absence_of_web_provenance_never_becomes_consent():
    assert _opening_loan_acquisition_allowed(_loan("bnf-pret", ebook_url="fictional")) is False
    assert _opening_loan_acquisition_allowed(object()) is False


def test_automatic_acquisition_facade_refuses_bnf_before_any_io():
    # A deliberately unusable service proves that the early refusal does not
    # even attempt to obtain a lock, inspect an account, or call a provider.
    from cifereca.services import LibraryService

    for force_refresh in (False, True):
        row = _loan("bnf-pret", ebook_url="https://example.invalid/signed-private-url")
        result = LibraryService._acquire_active_loan_automatically(
            object(), row, force_refresh=force_refresh,
        )
        assert result == {
            "status": "attention_required",
            "code": "bnf_web_explicit_only", "retry": "explicit_only",
        }
        assert "signed-private-url" not in repr(result)
