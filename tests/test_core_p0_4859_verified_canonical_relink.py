"""#4859 W4: relink only an already proven Kobo B after a physical move.

Real schema v14, published source/origin/artifact, disposable PDFs.
No provider, ADEPT, legacy backup, ledger or rights creation on relink.
"""
from __future__ import annotations

import os
import sqlite3
import json

import pytest

from cifereca.core.kobo_canonical_relink import (
    relink_verified_canonical_kobo as relink,
    CanonicalRelinkRejected,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, new_source, register, target, publish, BYTES, SHA,
)  # noqa: F401


@pytest.fixture
def published(store, tmp_path):
    source, snapshot = new_source(
        "A", provider="kobo", credential="vault-1",
        account="buyer-A", edition="book-1", right="purchase",
    )
    register(store, "A", "kobo", "vault-1")
    root = tmp_path / "EBooks"
    original = target(tmp_path, "A", "original.pdf")
    finished = publish(store, root, original, source, snapshot)
    path = root / "A" / "relocated.pdf"
    args = {
        "library_root": root,
        "identity_id": "A", "credential_id": "vault-1",
        "account_id": "buyer-A", "edition_id": "book-1",
        "right_id": "purchase", "branch_id": "purchases",
        "placement_id": finished.placement_id, "new_path": path,
    }
    return store, original, path, args, source, finished


def _read(store, table):
    with store._lock, store._connect() as db:
        return tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())


def _unchanged(store):
    return {
        table: _read(store, table)
        for table in (
            "source_references", "origin_links", "artifacts",
            "entitlements", "backup_items", "acquisition_ledger",
            "catalog_entries",
        )
    }


def test_exact_canonical_relink_only_mutates_existing_placement_and_journal(
    published, monkeypatch,
):
    store, original, path, args, source, finished = published
    before_positive = _unchanged(store)
    original.rename(path)
    original_bytes = path.read_bytes()
    for name in ("backup_put", "backup_list", "backup_get"):
        monkeypatch.setattr(
            store, name,
            lambda *_args, **_kw: pytest.fail("legacy backup authority forbidden"),
        )
    before_placements = _read(store, "placements")
    preview = relink(store, **args)
    assert preview["status"] == "preview"
    assert preview["changed"] == 0
    assert len(preview["plan_token"]) == 64
    assert _read(store, "placements") == before_placements
    confirmed = relink(store, confirmation=preview["plan_token"], **args)
    assert confirmed["status"] == "relinked"
    assert confirmed["changed"] == 1
    assert confirmed["legacy_backup_mutations"] == 0
    assert confirmed["rights_mutations"] == 0
    assert confirmed["file_mutations"] == 0
    assert _unchanged(store) == before_positive
    placement = store.placement_get(finished.placement_id)
    assert placement["path"] == str(path)
    assert placement["identity_id"] == "A"
    assert placement["artifact_id"] == finished.artifact_id
    assert placement["state"] == "present"
    assert path.read_bytes() == original_bytes == BYTES
    assert not original.exists()
    with store._lock, store._connect() as db:
        journals = db.execute(
            "SELECT value FROM metadata WHERE key LIKE "
            "'core.kobo.canonical-placement-relink.%'"
        ).fetchall()
    assert len(journals) == 1
    evidence = json.loads(journals[0][0])
    assert evidence["source_reference_id"] == finished.source_reference_id
    public = json.dumps(confirmed) + json.dumps(preview)
    for private in ("vault-1", "buyer-A", str(path), SHA, "book-1"):
        assert private not in public


@pytest.mark.parametrize("attack,reason", [
    ("old_exists", "canonical_relink_old_path_present"),
    ("cross_profile", "canonical_relink_file_unverified"),
    ("bad_bytes", "canonical_relink_file_unverified"),
    ("foreign_source", "canonical_relink_right_unverified"),
    ("origin_removed", "canonical_relink_origin_unverified"),
    ("account_disabled", "canonical_relink_scope_unavailable"),
    ("destination_claimed", "canonical_relink_destination_claimed"),
    ("symlink", "canonical_relink_file_unverified"),
])
def test_cannot_relink_legacy_or_unproven_B(
    published, tmp_path, attack, reason,
):
    store, original, path, args, source, finished = published
    if attack != "old_exists":
        original.rename(path)
    else:
        path.write_bytes(BYTES)
    if attack == "cross_profile":
        foreign = path.parent.parent / "B" / "same.pdf"
        foreign.parent.mkdir(parents=True, exist_ok=True)
        path.rename(foreign)
        args["new_path"] = foreign
    elif attack == "bad_bytes":
        path.write_bytes(b"%PDF-1.7\nUnrelated bytes\n")
    elif attack == "foreign_source":
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE source_references SET state='inactive' "
                "WHERE source_reference_id=?",
                (finished.source_reference_id,),
            )
    elif attack == "origin_removed":
        with store._lock, store._connect() as db:
            db.execute(
                "DELETE FROM origin_links WHERE source_reference_id=?",
                (finished.source_reference_id,),
            )
    elif attack == "account_disabled":
        store.provider_connection_put({
            "identity_id": "A", "provider_id": "kobo",
            "credential_id": "vault-1", "enabled": False,
        })
    elif attack == "destination_claimed":
        store.placement_put({
            "path": str(path), "identity_id": "A",
            "artifact_id": finished.artifact_id,
            "state": "present",
        })
    elif attack == "symlink":
        foreign = tmp_path / "outside.pdf"
        foreign.write_bytes(BYTES)
        path.unlink()
        path.symlink_to(foreign)
    before = _read(store, "placements")
    with pytest.raises(CanonicalRelinkRejected, match=reason):
        relink(store, **args)
    assert _read(store, "placements") == before


def test_stale_inode_even_with_same_bytes_refuses_relink_after_preview(published):
    store, original, path, args, source, finished = published
    original.rename(path)
    preview = relink(store, **args)
    replacement = path.with_suffix(".new")
    replacement.write_bytes(BYTES)
    os.replace(replacement, path)
    before = _read(store, "placements")
    with pytest.raises(CanonicalRelinkRejected, match="canonical_relink_plan_changed"):
        relink(store, confirmation=preview["plan_token"], **args)
    assert _read(store, "placements") == before


def test_changed_provider_origin_after_preview_invalidates_token(published):
    store, original, path, args, source, finished = published
    original.rename(path)
    preview = relink(store, **args)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE origin_links SET evidence_ref='different-receipt' "
            "WHERE source_reference_id=?",
            (finished.source_reference_id,),
        )
    before = _read(store, "placements")
    with pytest.raises(CanonicalRelinkRejected, match="canonical_relink_plan_changed"):
        relink(store, confirmation=preview["plan_token"], **args)
    assert _read(store, "placements") == before


def test_sqlite_failure_rolls_back_pointer_and_journal(published):
    store, original, path, args, source, finished = published
    original.rename(path)
    preview = relink(store, **args)
    before = _read(store, "placements")
    with store._lock, store._connect() as db:
        db.execute(
            "CREATE TRIGGER relink_failure BEFORE UPDATE ON placements "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END",
        )
    try:
        with pytest.raises(CanonicalRelinkRejected, match="canonical_relink_unavailable"):
            relink(store, confirmation=preview["plan_token"], **args)
        assert _read(store, "placements") == before
        with store._lock, store._connect() as db:
            assert db.execute(
                "SELECT count(*) FROM metadata WHERE key LIKE "
                "'core.kobo.canonical-placement-relink.%'"
            ).fetchone()[0] == 0
    finally:
        with store._lock, store._connect() as db:
            db.execute("DROP TRIGGER relink_failure")


def test_missing_explicit_confirmation_never_writes_or_moves_files(published):
    store, original, path, args, source, finished = published
    original.rename(path)
    before = _read(store, "placements")
    report = relink(store, **args)
    assert report["status"] == "preview"
    assert _read(store, "placements") == before
    assert path.read_bytes() == BYTES



def _offline(published, tmp_path):
    import json
    store, original, path, args, source, finished = published
    profile = tmp_path / "current-profile.json"
    profile.write_text(json.dumps({
        "identities": [
            {"id": "A", "label": "A", "directory_name": "A"},
        ],
        "library": {"directory": str(args["library_root"])},
    }), encoding="utf-8")
    return profile


def test_offline_profile_relink_and_cli_do_not_start_provider_or_legacy(
    published, tmp_path, monkeypatch, capsys,
):
    from cifereca.core import kobo_local_reconciliation as repair
    from cifereca import cli

    store, original, path, args, source, finished = published
    original.rename(path)
    profile = _offline(published, tmp_path)
    monkeypatch.setattr(repair, "config_file", lambda **_kw: profile)
    monkeypatch.setattr(repair, "database_file", lambda **_kw: store.path)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: pytest.fail("offline relink must not start Core/provider"),
    )
    before = _unchanged(store)
    argv = [
        "kobo-relink-canonical",
        "--credential", "vault-1", "--identity", "A",
        "--account-id", "buyer-A", "--item", "book-1",
        "--right", "purchase", "--branch", "purchases",
        "--placement", finished.placement_id,
        "--path", str(path),
    ]
    assert cli.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview"
    assert _unchanged(store) == before
    assert cli.main(argv + ["--confirm", preview["plan_token"]]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "relinked"
    assert receipt["rights_mutations"] == 0
    assert _unchanged(store) == before
    assert store.placement_get(finished.placement_id)["path"] == str(path)
    text = json.dumps(preview) + json.dumps(receipt)
    for private in ("vault-1", "buyer-A", "book-1", SHA, str(path)):
        assert private not in text


def test_offline_config_cannot_rebind_profile_folder(published, tmp_path):
    from cifereca.core import kobo_local_reconciliation as repair

    store, original, path, args, source, finished = published
    original.rename(path)
    profile = _offline(published, tmp_path)
    data = json.loads(profile.read_text())
    data["identities"][0]["directory_name"] = "another-owner"
    profile.write_text(json.dumps(data))
    before = _read(store, "placements")
    with pytest.raises(repair.ReconciliationRejected, match="identity_directory_unproven"):
        repair.reconcile_existing_profile(
            config_path=profile, database_path=store.path,
            canonical_relink=True,
            credential_id="vault-1", identity_id="A",
            account_id="buyer-A", edition_id="book-1",
            right_id="purchase", branch_id="purchases",
            placement_id=finished.placement_id,
            path=str(path),
        )
    assert _read(store, "placements") == before



def test_offline_profile_changed_during_sql_commit_rolls_back_B(
    published, tmp_path, monkeypatch,
):
    from cifereca.core import kobo_local_reconciliation as repair
    from cifereca.core import kobo_canonical_relink as canonical

    store, original, path, args, source, finished = published
    original.rename(path)
    profile = _offline(published, tmp_path)
    kwargs = {
        "config_path": profile, "database_path": store.path,
        "canonical_relink": True,
        "credential_id": "vault-1", "identity_id": "A",
        "account_id": "buyer-A", "edition_id": "book-1",
        "right_id": "purchase", "branch_id": "purchases",
        "placement_id": finished.placement_id, "path": str(path),
    }
    preview = repair.reconcile_existing_profile(**kwargs)
    before = _read(store, "placements")
    original_same = canonical._same_file
    calls = []

    def move_config_during_commit(file, earlier):
        calls.append(str(file))
        if len(calls) == 2:
            data = json.loads(profile.read_text())
            data["library"]["directory"] = str(tmp_path / "other-books")
            profile.write_text(json.dumps(data))
        return original_same(file, earlier)

    monkeypatch.setattr(canonical, "_same_file", move_config_during_commit)
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_plan_changed",
    ):
        repair.reconcile_existing_profile(
            **{**kwargs, "confirmation": preview["plan_token"]},
        )
    assert _read(store, "placements") == before
    with store._lock, store._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE "
            "'core.kobo.canonical-placement-relink.%'"
        ).fetchone()[0] == 0



def test_trigger_cannot_relabel_provider_receipt_during_relink(published):
    store, original, path, args, source, finished = published
    original.rename(path)
    preview = relink(store, **args)
    before = _read(store, "placements")
    before_origins = _read(store, "origin_links")
    with store._lock, store._connect() as db:
        db.execute(
            "CREATE TRIGGER relink_origin_swap AFTER UPDATE ON placements "
            "BEGIN UPDATE origin_links SET evidence_ref='forged-receipt' "
            "WHERE source_reference_id='" + finished.source_reference_id + "'; END"
        )
    try:
        with pytest.raises(
            CanonicalRelinkRejected, match="canonical_relink_file_unverified",
        ):
            relink(store, confirmation=preview["plan_token"], **args)
        assert _read(store, "placements") == before
        assert _read(store, "origin_links") == before_origins
    finally:
        with store._lock, store._connect() as db:
            db.execute("DROP TRIGGER relink_origin_swap")
