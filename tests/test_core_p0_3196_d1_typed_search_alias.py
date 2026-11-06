"""#3196 D1: Kobo's typed source must own a canonical local search alias.

SQLite + local synthetic file. No Kobo provider I/O, new rights, or file
movement. The legacy 0.7 receipt is deliberately contradictory in tests.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_core_library_placement_identity_scope import _service, _placement
from cifereca.core import library_projection


BODY = b"same epub contents"


@pytest.fixture
def case(tmp_path):
    service = _service(tmp_path)
    digest = hashlib.sha256(BODY).hexdigest()
    artifact = "sha256:" + digest
    service.storage.artifact_put({
        "artifact_id": artifact, "sha256": digest, "size": len(BODY),
        "media_format": "epub", "metadata": {},
    })
    placement = _placement(
        service, tmp_path / "library", owner="alice", filename="book.epub",
        artifact_id=artifact, item_id="book",
    )
    try:
        yield service, artifact, placement, tmp_path
    finally:
        service.close()


def _legacy(service, artifact):
    service.storage.acquisition_put({
        "acquisition_id": "legacy-0.7",
        "identity_id": "alice", "provider_id": "kobo",
        "credential_id": "vault", "account_id": "buyer",
        "item_id": "book", "artifact_id": artifact,
        "status": "complete", "title": "Old unverified archive",
    })


def _typed(service, *, source_id="typed", owner="alice", account="buyer",
           edition="book", right="purchase", credential="vault"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "state,evidence_kind,evidence_ref,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, owner, "kobo", credential, account, "purchases",
             edition, right, "purchase", "active",
             "provider_response", "synthetic-right-proof", 1, 1),
        )
    return source_id


def _origin(service, artifact, source_id="typed", *, owner="alice"):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO origin_links("
            "origin_link_id,origin_kind,identity_id,artifact_id,"
            "source_reference_id,evidence_ref,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            ("origin:" + source_id, "provider", owner, artifact,
             source_id, "synthetic-received-proof", 1, 1),
        )


def test_typed_kobo_ignores_complete_legacy_without_provider_origin(case):
    service, artifact, placement, _ = case
    _legacy(service, artifact)
    assert service.local_placements_for_sources("alice")[
        ("kobo", "vault", "book")
    ]["placement_id"] == placement
    _typed(service)
    before = (service.storage.acquisitions(), service.storage.placements())
    assert ("kobo", "vault", "book") not in (
        service.local_placements_for_sources("alice")
    )
    assert (service.storage.acquisitions(), service.storage.placements()) == before


def test_typed_kobo_uses_exact_origin_without_legacy_receipt(case):
    service, artifact, placement, _ = case
    _typed(service)
    _origin(service, artifact)
    assert service.storage.acquisitions(provider_id="kobo") == []
    assert service.local_placements_for_sources("alice")[
        ("kobo", "vault", "book")
    ]["placement_id"] == placement
    assert ("kobo", "vault", "book") not in (
        service.local_placements_for_sources("bob")
    )


@pytest.mark.parametrize("other_scope", ["other_account", "other_owner",
                                          "other_right"])
def test_typed_kobo_public_key_is_not_guessed_across_two_sources(
        case, other_scope):
    service, artifact, _placement_id, _ = case
    _legacy(service, artifact)
    _typed(service)
    _origin(service, artifact)
    kw = {
        "other_account": {"account": "other-buyer"},
        "other_owner": {"owner": "bob"},
        "other_right": {"right": "second-purchase"},
    }[other_scope]
    _typed(service, source_id="other", **kw)
    assert ("kobo", "vault", "book") not in (
        service.local_placements_for_sources("alice")
    )


def test_typed_kobo_origin_must_name_the_local_artifact(case, tmp_path):
    service, artifact, _placement_id, _ = case
    _legacy(service, artifact)
    other = "sha256:" + "b" * 64
    service.storage.artifact_put({
        "artifact_id": other, "sha256": "b" * 64,
        "size": 50, "media_format": "epub", "metadata": {},
    })
    _typed(service)
    _origin(service, other)
    assert ("kobo", "vault", "book") not in (
        service.local_placements_for_sources("alice")
    )


def test_typed_kobo_origin_requires_file_still_present(case):
    service, artifact, _placement_id, tmp_path = case
    _typed(service)
    _origin(service, artifact)
    (tmp_path / "library/alice/book.epub").unlink()
    assert ("kobo", "vault", "book") not in (
        service.local_placements_for_sources("alice")
    )


def test_untyped_kobo_retains_legacy_search_compatibility(case):
    service, artifact, placement, _ = case
    _legacy(service, artifact)
    assert service.local_placements_for_sources("alice")[
        ("kobo", "vault", "book")
    ]["placement_id"] == placement


def test_single_kobo_lookup_discards_legacy_after_typed_source_without_origin(
        case):
    service, artifact, _placement_id, _ = case
    _legacy(service, artifact)
    _typed(service)
    assert service.local_placement_for_source(
        provider_id="kobo", credential_id="vault", item_id="book",
        identity_id="alice",
    ) is None
    assert library_projection.local_placement_for_source(service,
        provider_id="kobo", credential_id="vault", item_id="book",
        identity_id="alice", account_id="buyer",
    ) is None


def test_single_kobo_lookup_requires_origin_and_exact_selected_borrower(case):
    service, artifact, placement, _ = case
    _legacy(service, artifact)
    _typed(service)
    _origin(service, artifact)
    args = dict(
        provider_id="kobo", credential_id="vault",
        item_id="book", identity_id="alice",
    )
    assert service.local_placement_for_source(
        **args,
    )["placement_id"] == placement
    assert library_projection.local_placement_for_source(service,
        **args, account_id="buyer",
    )["placement_id"] == placement
    assert library_projection.local_placement_for_source(service,
        **args, account_id="other-buyer",
    ) is None
    _typed(service, source_id="other", account="other-buyer")
    # The public three-coordinate key has become ambiguous, but an exact
    # borrower may still use her original canonical receipt and physical B.
    assert service.local_placement_for_source(**args) is None
    assert library_projection.local_placement_for_source(service,
        **args, account_id="buyer",
    )["placement_id"] == placement
    assert library_projection.local_placement_for_source(service,
        **args, account_id="other-buyer",
    ) is None


def test_single_untyped_kobo_still_supports_previous_exact_account_reader(
        case):
    service, artifact, placement, _ = case
    _legacy(service, artifact)
    assert library_projection.local_placement_for_source(service,
        provider_id="kobo", credential_id="vault", item_id="book",
        identity_id="alice", account_id="buyer",
    )["placement_id"] == placement


@pytest.mark.parametrize("unrelated_scope", ["credential", "edition"])
def test_d7_typed_source_scope_never_disables_unrelated_legacy_edition(
        case, unrelated_scope):
    """The typed-source veto must be exact, not provider- or profile-wide."""
    service, artifact, placement, _ = case
    _legacy(service, artifact)
    _typed(
        service,
        edition="other-book" if unrelated_scope == "edition" else "book",
        credential="other-vault" if unrelated_scope == "credential" else "vault",
    )
    alias = ("kobo", "vault", "book")
    assert service.local_placements_for_sources("alice")[
        alias
    ]["placement_id"] == placement
    assert service.local_placement_for_source(
        provider_id="kobo", credential_id="vault", item_id="book",
        identity_id="alice",
    )["placement_id"] == placement
    assert library_projection.local_placement_for_source(
        service, provider_id="kobo", credential_id="vault",
        item_id="book", identity_id="alice", account_id="buyer",
    )["placement_id"] == placement
