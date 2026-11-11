"""P0 #5054: the journal is byte evidence, never an entitlement."""
import json
import sqlite3
from pathlib import Path

from cifereca.core.canonical_recovery import _SCHEMA, _STAGED, _scope, _stage_id
from cifereca.core.explicit_selected_offer_stage import selected_offer_stage_current_in_transaction
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api.provider_rights_observation import ProviderRightsScope


def _fixture():
    scope = ProviderRightsScope("identity", "numilog", "credential", "account")
    source = SourceKey(scope, "edition", "edition", "purchase", "meslivres")
    offer = "selected:offer"
    path = Path("/tmp/fictional-result.epub")
    digest = "a" * 64
    receipt = "receipt:one"
    record = {
        "schema": _SCHEMA, "source": _scope(source), "offer_id": offer,
        "path": str(path), "sha256": digest, "receipt": receipt,
        "adept_receipt": "",
    }
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE acquisition_ledger (acquisition_id TEXT, identity_id TEXT,"
        "provider_id TEXT, credential_id TEXT, account_id TEXT, item_id TEXT,"
        "status TEXT, artifact_id TEXT, metadata_json TEXT)"
    )
    db.execute(
        "INSERT INTO acquisition_ledger VALUES(?,?,?,?,?,?,?,?,?)",
        (_stage_id(source, offer), scope.identity_id, scope.provider_id,
         scope.credential_id, scope.account_id, source.edition_id,
         _STAGED, None, json.dumps(record)),
    )
    return db, source, offer, path, digest, receipt


def _check(db, source, offer, path, digest, receipt):
    return selected_offer_stage_current_in_transaction(
        db, source, offer_id=offer, path=path,
        final_sha256=digest, delivery_receipt_ref=receipt,
    )


def test_exact_staged_receipt_matches_completed_bytes_not_pseudo_rights():
    db, source, offer, path, digest, receipt = _fixture()
    try:
        assert _check(db, source, offer, path, digest, receipt)
    finally:
        db.close()


def test_unstaged_or_foreign_offer_and_source_fail_closed():
    db, source, offer, path, digest, receipt = _fixture()
    try:
        foreign = SourceKey(
            ProviderRightsScope("foreign", "numilog", "credential", "account"),
            "edition", "edition", "purchase", "meslivres",
        )
        other_account = SourceKey(
            ProviderRightsScope("identity", "numilog", "credential", "other"),
            "edition", "edition", "purchase", "meslivres",
        )
        assert not _check(db, foreign, offer, path, digest, receipt)
        assert not _check(db, other_account, offer, path, digest, receipt)
        assert not _check(db, source, "other-offer", path, digest, receipt)
        assert not _check(db, source, offer, path, "b" * 64, receipt)
        assert not _check(db, source, offer, path, digest, "foreign-receipt")
        assert not _check(db, source, offer, Path("/tmp/foreign.epub"), digest, receipt)
    finally:
        db.close()


def test_completed_reused_or_corrupt_stage_cannot_reauthorize_publication():
    db, source, offer, path, digest, receipt = _fixture()
    try:
        db.execute("UPDATE acquisition_ledger SET status='complete'")
        assert not _check(db, source, offer, path, digest, receipt)
        db.execute("UPDATE acquisition_ledger SET status=?", (_STAGED,))
        db.execute("UPDATE acquisition_ledger SET artifact_id='sha256:historical'")
        assert not _check(db, source, offer, path, digest, receipt)
        db.execute("UPDATE acquisition_ledger SET artifact_id=NULL, metadata_json='{}'")
        assert not _check(db, source, offer, path, digest, receipt)
        db.execute("UPDATE acquisition_ledger SET metadata_json='not json'")
        assert not _check(db, source, offer, path, digest, receipt)
    finally:
        db.close()


def test_ambiguous_or_cross_bound_stage_never_matches():
    db, source, offer, path, digest, receipt = _fixture()
    try:
        db.execute("UPDATE acquisition_ledger SET credential_id='other'")
        assert not _check(db, source, offer, path, digest, receipt)
        db.execute("UPDATE acquisition_ledger SET credential_id='credential'")
        db.execute(
            "INSERT INTO acquisition_ledger SELECT * FROM acquisition_ledger"
        )
        assert not _check(db, source, offer, path, digest, receipt)
    finally:
        db.close()
