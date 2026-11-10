"""P0 #5054: provider-neutral record scoping is structural, not rights proof.

No vendor implementation, fixture or legacy wrapper may be imported here.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.core.credential_scoped_record import exact_credential_scoped_record


def make(provider="future-store", credential="cred-a", lid="edition-1",
         variant="ab1234567890def0"):
    digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:20]
    return f"{provider}:{digest}:{lid}:{variant}"


@pytest.mark.parametrize("provider", ["library-a", "future-store", "catalogue"])
def test_unknown_providers_can_share_format_but_not_credentials(provider):
    record = make(provider=provider)
    assert exact_credential_scoped_record(
        record, provider_id=provider, credential_id="cred-a",
    ) == record
    assert exact_credential_scoped_record(
        record, provider_id=provider, credential_id="cred-b",
    ) is None
    assert exact_credential_scoped_record(
        record, provider_id="other-provider", credential_id="cred-a",
    ) is None


@pytest.mark.parametrize("corrupt", [
    lambda s: s.replace("edition-1", "../private"),
    lambda s: s.replace("edition-1", "x" * 161),
    lambda s: s[:-1] + "G",
    lambda s: s[:-16] + "0" * 15,
    lambda s: s.upper(),
    lambda s: s + ":extra",
    lambda s: s.replace(":", "::", 1),
    lambda s: s.replace("edition-1", "x\nsecret"),
])
def test_hostile_record_ids_cannot_cross_source_scope(corrupt):
    assert exact_credential_scoped_record(
        corrupt(make()), provider_id="future-store", credential_id="cred-a",
    ) is None


@pytest.mark.parametrize(("credential", "provider"), [
    ("", "library-a"), (None, "library-a"), (1, "library-a"),
    ("cred", ""), ("cred", "LIBRARY-A"), ("cred", "provider/name"),
])
def test_invalid_source_identity_fails_closed(credential, provider):
    assert exact_credential_scoped_record(
        make(), provider_id=provider, credential_id=credential,
    ) is None


def test_scoped_record_is_not_a_loan_or_purchase_proof():
    # The contract emits only the same record ID, no entitlement,
    # source session lease, credential, download URL or authorization.
    ref = make()
    assert exact_credential_scoped_record(
        ref, provider_id="future-store", credential_id="cred-a",
    ) == ref
