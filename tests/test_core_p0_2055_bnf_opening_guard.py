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
def test_all_providers_default_deny_without_explicit_live_session(provider):
    # #3121: the historical name-based exemption is gone. A direct call with
    # an arbitrary positive loan is no longer permission to auto-acquire.
    assert _opening_loan_acquisition_allowed(_loan(provider, ebook_url="fictional")) is False
    assert _opening_loan_acquisition_allowed(_loan(provider, raw={"fixture": True})) is False
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
            "code": "loan_auto_explicit_only", "retry": "explicit_only",
        }
        assert "signed-private-url" not in repr(result)


@pytest.mark.parametrize("provider", ["bnf-pret", "future-library", "paris"])
def test_connected_provider_without_auto_contract_is_denied_before_lock(
    provider, monkeypatch,
):
    """No source can stage an automatic loan merely because it has a Web row."""
    from dataclasses import replace
    from types import SimpleNamespace
    from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
    from cifereca.core.automatic_loan_source_policy import auto_loan_refusal

    caps = ProviderCapabilities(loans=True, digital_download=provider != "bnf-pret")
    descriptor = ProviderDescriptor(
        id=provider, display_name="Synthetic", description="Offline", state="ready",
        capabilities=caps,
    )
    session = SimpleNamespace(
        descriptor=descriptor, provider_id=provider, credential_id="fictional-credential",
        connected=True,
    )
    registry = {provider + "::fictional-credential": session}
    assert auto_loan_refusal(SimpleNamespace(_sessions=registry), provider,
                            "fictional-credential") == "loan_auto_explicit_only"

    # Capability declaration is not an independent right, but permits the
    # preflight to continue for an exact installed, connected opt-in source.
    opted = replace(descriptor, capabilities=replace(
        caps, opening_loan_auto_acquisition=True, digital_download=True,
    ))
    session.descriptor = opted
    assert auto_loan_refusal(SimpleNamespace(_sessions=registry), provider,
                            "fictional-credential") is None
    session.connected = False
    assert auto_loan_refusal(SimpleNamespace(_sessions=registry), provider,
                            "fictional-credential") == "loan_auto_explicit_only"
    session.connected = True
    session.provider_id = "foreign-source"
    assert auto_loan_refusal(SimpleNamespace(_sessions=registry), provider,
                            "fictional-credential") == "loan_auto_explicit_only"


def test_bnf_registered_session_denied_without_lock_or_supplier_io(monkeypatch):
    from types import SimpleNamespace
    from cifereca.services import LibraryService
    from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor
    import cifereca.services as facade

    row = _loan("bnf-pret", ebook_url="https://example.invalid/private-acsm")
    descriptor = ProviderDescriptor(
        id="bnf-pret", display_name="Synthetic", description="Offline",
        state="ready", capabilities=ProviderCapabilities(
            loans=True, loan_auto_materialization=True,
        ),
    )
    session = SimpleNamespace(
        descriptor=descriptor, provider_id="bnf-pret",
        credential_id=row.source_credential, connected=True,
    )
    host = SimpleNamespace(_sessions={
        "bnf-pret::" + row.source_credential: session,
    })
    def forbidden(*_args, **_kwargs):
        pytest.fail("no lock, network, staging or rights mutation permitted")
    monkeypatch.setattr(facade, "InterProcessLock", forbidden)
    denied = LibraryService._acquire_active_loan_automatically(host, row)
    assert denied == {
        "status": "attention_required", "code": "loan_auto_explicit_only",
        "retry": "explicit_only",
    }
    assert "private-acsm" not in repr(denied)
