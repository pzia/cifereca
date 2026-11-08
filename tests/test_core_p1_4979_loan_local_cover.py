"""P1 #4979: profile-local loan cover requires an exact canonical OriginLink.

All inputs are synthetic, and no filesystem, provider or GTK is accessed.
"""
from __future__ import annotations

from dataclasses import replace

from cifereca.core.loan_cover_fallback import proven_local_loan_cover_urls
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.models import Loan


ARTIFACT_A = "sha256:" + "a" * 64
ARTIFACT_B = "sha256:" + "b" * 64
URL_A = "file:///synthetic/cover-a.png"
URL_B = "file:///synthetic/cover-b.png"


def loan(**changes):
    base = Loan(
        id="loan-1", provider="paris", medium="digital",
        record_id="edition-1", title="Titre strictement descriptif",
        source_provider="paris", source_credential="card-A",
        source_account_id="", identity_id="alice",
    )
    return replace(base, **changes)


def origin(artifact=ARTIFACT_A, *, owner="alice", provider="paris",
           credential="card-A", account="", edition="edition-1", kind="loan"):
    return artifact, owner, provider, credential, account, edition, kind


def local(artifact=ARTIFACT_A, url=URL_A, *, owner="alice",
          tier="embedded", source="epub", status="selected"):
    return {
        "identity_id": owner, "artifact_id": artifact,
        "thumbnail_url": url,
        "metadata": {"cover_resolution": {
            "status": status, "tier": tier, "source": source, "url": url,
        }},
    }


def resolve(loans, origins=None, local_rows=None, identity="alice"):
    return proven_local_loan_cover_urls(
        loans, identity_id=identity,
        origins=[origin()] if origins is None else origins,
        local_rows=[local()] if local_rows is None else local_rows,
    )


def test_embedded_cover_proven_by_exact_loan_origin_is_projected():
    target = loan()
    assert resolve([target]) == {loan_source_key(target): URL_A}
    # Upstream primary account "" is a known borrower, not None.
    assert resolve([loan(source_account_id=None)]) == {}
    assert resolve([loan(identity_id="bob")]) == {}


def test_foreign_provider_account_credential_record_or_origin_kind_never_match():
    target = loan()
    for wrong in (
        origin(owner="bob"), origin(provider="kobo"),
        origin(credential="card-B"), origin(account="other"),
        origin(edition="edition-2"), origin(kind="purchase"),
    ):
        assert resolve([target], origins=[wrong]) == {}
    for variant in (
        loan(source_provider="kobo"), loan(source_credential="card-B"),
        loan(source_account_id="different"), loan(record_id="other"),
    ):
        assert resolve([variant]) == {}
    assert resolve([target], identity="bob") == {}
    assert resolve([target], origins=[]) == {}


def test_ambiguous_artifacts_or_covers_do_not_choose_a_random_source():
    target = loan()
    assert resolve(
        [target], origins=[origin(), origin(ARTIFACT_B)],
        local_rows=[local(), local(ARTIFACT_B, URL_B)],
    ) == {}
    assert resolve(
        [target], origins=[origin(), origin(ARTIFACT_B)],
        local_rows=[local(), local(ARTIFACT_B, URL_A)],
    ) == {}
    assert resolve(
        [target], local_rows=[local(), local(url=URL_B)],
    ) == {}
    assert resolve([target], local_rows=[local(), local()]) == {
        loan_source_key(target): URL_A,
    }


def test_nonlocal_catalog_remote_weak_and_malformed_cover_evidence_fail_closed():
    for invalid in (
        local(owner="bob"),
        local(tier="catalog"), local(tier="origin"),
        local(source="catalog-entry"), local(status="ambiguous"),
        local(url="https://example.test/cover.png"),
        local(url="file://other-host/cover.png"),
        local(url="file:///cache/cover.png?token=secret"),
        local(url="file:///cache/cover.png#fragment"),
        {"artifact_id": ARTIFACT_A, "identity_id": "alice",
         "thumbnail_url": URL_A, "metadata": {}},
        {"artifact_id": ARTIFACT_A, "identity_id": "alice",
         "thumbnail_url": URL_A, "metadata": {
             "cover_resolution": {"status": "selected", "tier": "embedded",
                                  "source": "epub", "url": URL_B},
         }},
    ):
        assert resolve([loan()], local_rows=[invalid]) == {}


def test_existing_loan_cover_is_not_replaced_and_unproven_loans_stay_empty():
    present = loan(thumbnail_url="https://covers.example.test/remote.png")
    assert resolve([present]) == {}
    assert resolve([loan(source_account_id=7)]) == {}
    assert resolve([loan(record_id="", id="")]) == {}
    assert resolve([loan()], origins=[origin(kind="purchase")]) == {}


def test_no_cross_profile_proof_or_implicit_default_on_missing_identity():
    assert resolve([loan(identity_id=None)]) == {}
    assert resolve([loan()], identity="") == {}
    assert resolve([loan()], local_rows=[local(owner="bob")]) == {}
    assert resolve([loan()], origins=[origin(owner="bob")]) == {}
