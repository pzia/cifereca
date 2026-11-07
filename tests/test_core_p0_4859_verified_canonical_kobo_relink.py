"""#4859 W4: an already published Core B can be relinked without v0.7 authority.

Real v14 SQLite, real tiny PDF, exact positive Kobo SourceReference/OriginLink
created by the production publisher. No plugin, Vault, GET or ADEPT.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cifereca.core.kobo_canonical_relink import (
    CanonicalRelinkRejected, relink_verified_canonical_kobo,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    SHA, BYTES, store, new_source, register, target, publish,  # noqa: F401
)


@pytest.fixture
def fixture_b(store, tmp_path):
    source, snapshot = new_source(
        profile="A", provider="kobo", credential="card-A",
        account="buyer-A", edition="kobo-book", right="owned",
    )
    register(store, "A", "kobo", "card-A", account="buyer-A")
    old = target(tmp_path, "A", "purchased.pdf")
    result = publish(store, tmp_path / "EBooks", old, source, snapshot)
    assert result.changed
    new = old.with_name("moved.pdf")
    args = {
        "library_root": tmp_path / "EBooks",
        "identity_id": "A", "credential_id": "card-A",
        "account_id": "buyer-A", "edition_id": "kobo-book",
        "right_id": "owned", "branch_id": "purchases",
        "placement_id": result.placement_id, "new_path": new,
    }
    return store, old, new, args


def _records(storage):
    with storage._lock, storage._connect() as db:
        return {
            name: tuple(db.execute("SELECT * FROM " + name + " ORDER BY 1"))
            for name in (
                "backup_items", "entitlements", "acquisition_ledger",
                "artifacts", "source_references", "origin_links",
                "placements", "catalog_entries", "metadata",
            )
        }


def _relink(fixture_b, *, confirmation=None, **change):
    storage, _old, _new, args = fixture_b
    return relink_verified_canonical_kobo(
        storage, **{**args, **change}, confirmation=confirmation,
    )


def _moved(fixture_b):
    _, old, new, _ = fixture_b
    old.rename(new)


def test_exact_core_b_preview_and_confirmation_only_move_existing_locator(
    fixture_b,
):
    storage, old, new, args = fixture_b
    _moved(fixture_b)
    baseline = _records(storage)
    preview = _relink(fixture_b)
    assert preview["status"] == "preview" and preview["ok"] is True
    assert preview["changed"] == 0
    assert preview["legacy_backup_mutations"] == 0
    assert _records(storage) == baseline

    done = _relink(fixture_b, confirmation=preview["plan_token"])
    assert done["status"] == "relinked" and done["ok"] is True
    assert done["changed"] == 1 and done["rights_mutations"] == 0
    assert done["file_mutations"] == 0
    after = _records(storage)
    for table in baseline:
        if table not in {"placements", "metadata"}:
            assert after[table] == baseline[table], table
    placement = storage.placement_get(args["placement_id"])
    assert placement["path"] == str(new)
    assert placement["state"] == "present"
    assert placement["artifact_id"] == "sha256:" + SHA
    assert placement["provider_id"] is None
    assert new.read_bytes() == BYTES and not old.exists()
    keys = [key for key, _value in after["metadata"]
            if key.startswith("core.kobo.canonical-placement-relink.")]
    assert len(keys) == 1
    for public in (json.dumps(preview), json.dumps(done)):
        for secret in ("card-A", "buyer-A", str(new), "kobo-book", SHA):
            assert secret not in public


def test_missing_old_path_is_mandatory_before_any_pointer_move(fixture_b):
    storage, old, new, _ = fixture_b
    new.write_bytes(old.read_bytes())
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_old_path_present",
    ):
        _relink(fixture_b)
    assert _records(storage) == before
    assert old.read_bytes() == new.read_bytes() == BYTES


@pytest.mark.parametrize("scope", [
    {"identity_id": "other"},
    {"account_id": "other-borrower"},
    {"credential_id": "other-card"},
    {"branch_id": "loans"},
    {"right_id": "other-right"},
    {"edition_id": "other-book"},
])
def test_foreign_source_never_claims_existing_provider_origin(fixture_b, scope):
    storage, _old, new, _ = fixture_b
    _moved(fixture_b)
    before = _records(storage)
    with pytest.raises(CanonicalRelinkRejected):
        _relink(fixture_b, **scope)
    assert _records(storage) == before
    assert new.read_bytes() == BYTES


@pytest.mark.parametrize("corrupt", [
    "source_inactive", "source_synthetic", "missing_origin",
    "origin_not_provider", "wrong_owner", "wrong_artifact",
    "legacy_label", "artifact_size_mismatch",
])
def test_unattested_or_poisoned_B_cannot_be_promoted(corrupt, fixture_b):
    storage, _old, new, args = fixture_b
    _moved(fixture_b)
    with storage._lock, storage._connect() as db:
        pid = args["placement_id"]
        if corrupt == "source_inactive":
            db.execute("UPDATE source_references SET state='inactive'")
        elif corrupt == "source_synthetic":
            db.execute("UPDATE source_references SET evidence_kind='historical_receipt'")
        elif corrupt == "missing_origin":
            db.execute("DELETE FROM origin_links WHERE origin_kind='provider'")
        elif corrupt == "origin_not_provider":
            # Published origin coordinates are protected by an immutable SQL
            # trigger. Removing that supplier proof is the valid negative
            # case; do not mutate an OriginLink into a different origin_kind.
            db.execute("DELETE FROM origin_links WHERE origin_kind='provider'")
        elif corrupt == "wrong_owner":
            db.execute("UPDATE placements SET identity_id='other' WHERE placement_id=?", (pid,))
        elif corrupt == "wrong_artifact":
            db.execute("UPDATE placements SET artifact_id='sha256:'||? WHERE placement_id=?", ("b" * 64, pid))
        elif corrupt == "legacy_label":
            db.execute("UPDATE placements SET provider_id='kobo',source_item_id='kobo-book' WHERE placement_id=?", (pid,))
        else:
            db.execute("UPDATE artifacts SET size=size+1 WHERE sha256=?", (SHA,))
    before = _records(storage)
    with pytest.raises(CanonicalRelinkRejected):
        _relink(fixture_b)
    assert _records(storage) == before


@pytest.mark.parametrize("destination", [
    "foreign_profile", "symlink", "different_bytes", "already_placed",
])
def test_uncertain_destination_or_other_profile_cannot_be_adopted(
    fixture_b, destination,
):
    storage, old, new, args = fixture_b
    old.unlink()
    if destination == "foreign_profile":
        new = args["library_root"] / "B" / "book.pdf"
        new.parent.mkdir()
        new.write_bytes(BYTES)
    elif destination == "symlink":
        file = new.with_name("real.pdf")
        file.write_bytes(BYTES)
        new.symlink_to(file)
    elif destination == "different_bytes":
        new.write_bytes(b"%PDF-1.7\nwrong content\n")
    else:
        new.write_bytes(BYTES)
        storage.placement_put({
            "path": str(new), "identity_id": "A",
            "artifact_id": "sha256:" + SHA, "state": "present",
        })
    before = _records(storage)
    with pytest.raises(CanonicalRelinkRejected):
        _relink(fixture_b, new_path=new)
    assert _records(storage) == before


def test_stale_preview_refuses_inode_aba_even_when_sha_is_identical(fixture_b):
    storage, old, new, _ = fixture_b
    _moved(fixture_b)
    token = _relink(fixture_b)["plan_token"]
    replacement = new.with_name("replacement.pdf")
    replacement.write_bytes(BYTES)
    os.replace(replacement, new)
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_plan_changed",
    ):
        _relink(fixture_b, confirmation=token)
    assert _records(storage) == before


def test_sql_rollback_on_injected_insert_failure_and_exact_replay(fixture_b):
    storage, old, new, args = fixture_b
    _moved(fixture_b)
    token = _relink(fixture_b)["plan_token"]
    with storage._lock, storage._connect() as db:
        db.execute(
            "CREATE TRIGGER no_relink_journal BEFORE INSERT ON metadata "
            "WHEN NEW.key LIKE 'core.kobo.canonical-placement-relink.%' "
            "BEGIN SELECT RAISE(ABORT, 'no_new_audit'); END",
        )
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_unavailable",
    ):
        _relink(fixture_b, confirmation=token)
    assert _records(storage) == before
    assert storage.placement_get(args["placement_id"])["path"] == str(old)
    assert new.read_bytes() == BYTES
    with storage._lock, storage._connect() as db:
        db.execute("DROP TRIGGER no_relink_journal")
    assert _relink(fixture_b, confirmation=token)["status"] == "relinked"


def test_context_rebound_at_commit_boundary_rolls_back_placement_and_journal(
    fixture_b,
):
    storage, _old, new, _ = fixture_b
    _moved(fixture_b)
    token = _relink(fixture_b)["plan_token"]
    calls = []
    def current():
        calls.append(True)
        return len(calls) < 2
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_plan_changed",
    ):
        _relink(fixture_b, confirmation=token, context_current=current)
    assert _records(storage) == before
    assert len(calls) == 2
    assert new.read_bytes() == BYTES


def test_rights_or_origin_rebound_between_preview_and_commit_is_rejected(
    fixture_b,
):
    storage, _old, new, _ = fixture_b
    _moved(fixture_b)
    token = _relink(fixture_b)["plan_token"]
    with storage._lock, storage._connect() as db:
        db.execute("UPDATE source_references SET evidence_ref='different-fresh-claim'")
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_plan_changed",
    ):
        _relink(fixture_b, confirmation=token)
    assert _records(storage) == before


def test_old_path_symlink_is_never_overwritten(fixture_b):
    storage, old, new, _ = fixture_b
    _moved(fixture_b)
    old.symlink_to(new)
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_old_path_present",
    ):
        _relink(fixture_b)
    assert _records(storage) == before
    assert old.is_symlink() and new.read_bytes() == BYTES


def test_reverse_relink_requires_fresh_physical_proof_and_keeps_both_journals(
    fixture_b,
):
    """Undo is another confirmed B relink, never a SQL undo of a provider right."""
    storage, old, new, args = fixture_b
    original_source = _records(storage)["source_references"]
    original_origins = _records(storage)["origin_links"]
    _moved(fixture_b)
    forward = _relink(fixture_b)["plan_token"]
    assert _relink(fixture_b, confirmation=forward)["status"] == "relinked"

    # A reverse SQL pointer change must never pass while the physical file
    # is still at the current canonical pathname.
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_old_path_present",
    ):
        _relink(fixture_b, new_path=old)
    new.rename(old)
    reverse = _relink(fixture_b, new_path=old)["plan_token"]
    assert reverse != forward
    assert _relink(fixture_b, confirmation=reverse, new_path=old)["status"] == "relinked"
    assert storage.placement_get(args["placement_id"])["path"] == str(old)
    assert old.read_bytes() == BYTES and not new.exists()
    after = _records(storage)
    assert after["source_references"] == original_source
    assert after["origin_links"] == original_origins
    assert len([
        key for key, _ in after["metadata"]
        if key.startswith("core.kobo.canonical-placement-relink.")
    ]) == 2


def test_reverse_relink_cannot_erase_newly_rebound_provider_proof(
    fixture_b,
):
    storage, old, new, _ = fixture_b
    _moved(fixture_b)
    first = _relink(fixture_b)["plan_token"]
    assert _relink(fixture_b, confirmation=first)["status"] == "relinked"
    new.rename(old)
    with storage._lock, storage._connect() as db:
        db.execute("UPDATE source_references SET state='inactive'")
    before = _records(storage)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_right_unverified",
    ):
        _relink(fixture_b, new_path=old)
    assert _records(storage) == before
    assert old.read_bytes() == BYTES
