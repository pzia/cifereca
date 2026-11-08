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


@pytest.fixture(autouse=True)
def _private_acquisition_lock(tmp_path, monkeypatch):
    # An isolated file lock prevents cross-worker CI contention while still
    # exercising the same real InterProcessLock acquisition/rollback path.
    from cifereca.core import kobo_canonical_relink as canonical
    monkeypatch.setattr(
        canonical, "acquisition_lock_file",
        lambda: tmp_path / "canonical-relink.lock",
    )


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


def test_changed_provider_source_evidence_after_preview_invalidates_token(published):
    store, original, path, args, source, finished = published
    original.rename(path)
    preview = relink(store, **args)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE source_references SET evidence_ref='different-receipt' "
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
            # A trigger cannot rewrite an immutable provider OriginLink.
            # SQLite raises before the post-update witness; the whole
            # placement transaction still rolls back atomically.
            CanonicalRelinkRejected, match="canonical_relink_unavailable",
        ):
            relink(store, confirmation=preview["plan_token"], **args)
        assert _read(store, "placements") == before
        assert _read(store, "origin_links") == before_origins
    finally:
        with store._lock, store._connect() as db:
            db.execute("DROP TRIGGER relink_origin_swap")


def test_empty_primary_kobo_borrower_relinks_only_preexisting_attested_B(
    store, tmp_path,
):
    source, snapshot = new_source(
        "A", provider="kobo", credential="vault-primary",
        account="", edition="empty-primary-edition",
        right="owned", branch="purchases",
    )
    register(store, "A", "kobo", "vault-primary", account="")
    root = tmp_path / "EBooks"
    original = target(tmp_path, "A", "primary-original.pdf")
    published = publish(store, root, original, source, snapshot)
    moved = original.with_name("primary-moved.pdf")
    original.rename(moved)
    kwargs = {
        "library_root": root, "identity_id": "A",
        "credential_id": "vault-primary", "account_id": "",
        "edition_id": "empty-primary-edition", "right_id": "owned",
        "branch_id": "purchases", "placement_id": published.placement_id,
        "new_path": moved,
    }
    unchanged = _unchanged(store)
    before = _read(store, "placements")
    preview = relink(store, **kwargs)
    assert preview["status"] == "preview"
    assert _read(store, "placements") == before
    confirmed = relink(store, confirmation=preview["plan_token"], **kwargs)
    assert confirmed["status"] == "relinked"
    assert confirmed["legacy_backup_mutations"] == 0
    assert confirmed["rights_mutations"] == 0
    assert confirmed["file_mutations"] == 0
    assert _unchanged(store) == unchanged
    assert store.placement_get(published.placement_id)["path"] == str(moved)
    assert moved.read_bytes() == BYTES and not original.exists()


@pytest.mark.parametrize("foreign", [
    None, " ", "kobo:vault-primary:primary", "other-borrower",
])
def test_empty_primary_relink_never_admits_missing_or_aliased_borrower(
    store, tmp_path, foreign,
):
    source, snapshot = new_source(
        "A", provider="kobo", credential="vault-primary",
        account="", edition="primary-edition", right="owned",
    )
    register(store, "A", "kobo", "vault-primary", account="")
    root = tmp_path / "EBooks"
    original = target(tmp_path, "A", "origin.pdf")
    published = publish(store, root, original, source, snapshot)
    moved = original.with_name("moved.pdf")
    original.rename(moved)
    arguments = {
        "library_root": root, "identity_id": "A",
        "credential_id": "vault-primary", "account_id": foreign,
        "edition_id": "primary-edition", "right_id": "owned",
        "branch_id": "purchases", "placement_id": published.placement_id,
        "new_path": moved,
    }
    baseline = _read(store, "placements")
    with pytest.raises(CanonicalRelinkRejected):
        relink(store, **arguments)
    assert _read(store, "placements") == baseline


def test_exact_published_kobo_loan_relinks_without_promoting_a_purchase(
    store, tmp_path,
):
    """W4 relink of a canonical loan must retain its *loan* SourceReference."""
    source, snapshot = new_source(
        "A", provider="kobo", credential="vault-loan",
        account="borrower-loan", branch="loans",
        edition="loan-edition", right="borrowed-right", kind="loan",
    )
    register(store, "A", "kobo", "vault-loan", account="borrower-loan")
    root = tmp_path / "EBooks"
    original = target(tmp_path, "A", "original-loan.pdf")
    completed = publish(store, root, original, source, snapshot)
    moved = original.with_name("relocated-loan.pdf")
    original.rename(moved)
    kwargs = {
        "library_root": root, "identity_id": "A",
        "credential_id": "vault-loan", "account_id": "borrower-loan",
        "edition_id": "loan-edition", "right_id": "borrowed-right",
        "branch_id": "loans", "reference_kind": "loan",
        "placement_id": completed.placement_id, "new_path": moved,
    }
    immutable = _unchanged(store)
    placements = _read(store, "placements")

    # A purchase with exactly the same other coordinates cannot borrow
    # a preexisting loan origin or mutate its Placement.
    with pytest.raises(CanonicalRelinkRejected, match="canonical_relink_right_unverified"):
        relink(store, **{**kwargs, "reference_kind": "purchase"})
    assert _unchanged(store) == immutable
    assert _read(store, "placements") == placements

    preview = relink(store, **kwargs)
    assert preview["status"] == "preview"
    assert _read(store, "placements") == placements
    result = relink(store, confirmation=preview["plan_token"], **kwargs)
    assert result["status"] == "relinked" and result["changed"] == 1
    assert result["rights_mutations"] == 0
    assert result["file_mutations"] == 0
    assert result["legacy_backup_mutations"] == 0
    assert _unchanged(store) == immutable
    assert store.placement_get(completed.placement_id)["path"] == str(moved)
    assert moved.read_bytes() == BYTES and not original.exists()


@pytest.mark.parametrize("kind", ("", "Loan", None, "unknown", "loan "))
def test_relink_rejects_invalid_reference_kind_before_sql_mutation(
    published, kind,
):
    store, original, moved, args, _source, _completed = published
    original.rename(moved)
    before = _read(store, "placements")
    with pytest.raises(CanonicalRelinkRejected, match="invalid_relink_request"):
        relink(store, **{**args, "reference_kind": kind})
    assert _read(store, "placements") == before


def test_offline_cli_selects_canonical_loan_kind_without_core_start(
    store, tmp_path, monkeypatch, capsys,
):
    from cifereca.core import kobo_local_reconciliation as repair
    from cifereca import cli

    source, snapshot = new_source(
        "A", provider="kobo", credential="vault-loan-cli",
        account="", edition="loan-edition-cli", right="loan-right",
        branch="loans", kind="loan",
    )
    register(store, "A", "kobo", "vault-loan-cli", account="")
    root = tmp_path / "EBooks"
    original = target(tmp_path, "A", "loan-cli.pdf")
    published = publish(store, root, original, source, snapshot)
    moved = original.with_name("loan-cli-moved.pdf")
    original.rename(moved)
    profile = tmp_path / "profile-loan-cli.json"
    profile.write_text(json.dumps({
        "identities": [{"id": "A", "label": "A", "directory_name": "A"}],
        "library": {"directory": str(root)},
    }), encoding="utf-8")

    monkeypatch.setattr(repair, "config_file", lambda **_kw: profile)
    monkeypatch.setattr(repair, "database_file", lambda **_kw: store.path)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: pytest.fail("offline loan relink must not open Core"),
    )
    arguments = [
        "kobo-relink-canonical", "--credential", "vault-loan-cli",
        "--identity", "A", "--account-id", "",
        "--item", "loan-edition-cli", "--right", "loan-right",
        "--branch", "loans", "--kind", "loan",
        "--placement", published.placement_id, "--path", str(moved),
    ]
    immutable = _unchanged(store)
    assert cli.main(arguments) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "preview"
    assert _unchanged(store) == immutable
    assert cli.main(arguments + ["--confirm", preview["plan_token"]]) == 0
    done = json.loads(capsys.readouterr().out)
    assert done["status"] == "relinked"
    assert done["rights_mutations"] == 0
    assert _unchanged(store) == immutable
    assert store.placement_get(published.placement_id)["path"] == str(moved)


def test_exact_journal_undo_relinks_only_after_physical_return(published):
    """W4/D5: undo is a new attested reverse placement, never v0.7 rewind."""
    store, original, moved, args, _source, _completed = published
    unchanged = _unchanged(store)
    original.rename(moved)
    forward = relink(store, **args)
    done = relink(store, **args, confirmation=forward["plan_token"])
    operation = done["operation_id"]
    assert len(operation) == 64
    assert _unchanged(store) == unchanged

    # A SQL-only undo would strand B while its actual bytes remain at moved.
    with pytest.raises(CanonicalRelinkRejected):
        relink(
            store, **{**args, "new_path": original},
            undo_operation=operation,
        )
    assert store.placement_get(args["placement_id"])["path"] == str(moved)
    moved.rename(original)
    before_preview = _read(store, "placements")
    reverse = relink(
        store, **{**args, "new_path": original},
        undo_operation=operation,
    )
    assert reverse["status"] == "preview"
    assert reverse["changed"] == 0
    assert _read(store, "placements") == before_preview
    # A normal reverse token cannot be used for a journal-bound undo.
    plain = relink(store, **{**args, "new_path": original})
    assert plain["plan_token"] != reverse["plan_token"]
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_plan_changed",
    ):
        relink(
            store, **{**args, "new_path": original},
            undo_operation=operation, confirmation=plain["plan_token"],
        )
    assert _read(store, "placements") == before_preview
    finished = relink(
        store, **{**args, "new_path": original},
        undo_operation=operation, confirmation=reverse["plan_token"],
    )
    assert finished["status"] == "relinked"
    assert finished["changed"] == 1
    assert finished["legacy_backup_mutations"] == 0
    assert finished["rights_mutations"] == 0
    assert finished["file_mutations"] == 0
    assert store.placement_get(args["placement_id"])["path"] == str(original)
    assert original.read_bytes() == BYTES and not moved.exists()
    assert _unchanged(store) == unchanged
    with store._lock, store._connect() as db:
        journals = [
            json.loads(record) for (record,) in db.execute(
                "SELECT value FROM metadata WHERE key LIKE "
                "'core.kobo.canonical-placement-relink.%'"
            )
        ]
    assert len(journals) == 2
    assert any(row.get("undo_of") == operation for row in journals)
    assert sum(row.get("undo_of") is None for row in journals) == 1

    # Stale undo is consumed by the reverse placement and cannot be replayed.
    with pytest.raises(CanonicalRelinkRejected):
        relink(
            store, **{**args, "new_path": original},
            undo_operation=operation, confirmation=reverse["plan_token"],
        )


@pytest.mark.parametrize("tamper", (
    "wrong_operation", "changed_source", "changed_origin", "changed_placement",
    "same_sha_new_inode", "duplicate_json_field",
))
def test_journal_undo_rejects_rebound_right_or_file_without_mutation(
    published, tamper,
):
    store, original, moved, args, source, completed = published
    original.rename(moved)
    forward = relink(store, **args)["plan_token"]
    operation = relink(store, **args, confirmation=forward)["operation_id"]
    moved.rename(original)
    if tamper == "wrong_operation":
        operation = "f" * 64
    elif tamper == "changed_source":
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE source_references SET evidence_ref='changed-provider-evidence'"
            )
    elif tamper == "changed_origin":
        with store._lock, store._connect() as db:
            db.execute("DELETE FROM origin_links WHERE origin_kind='provider'")
    elif tamper == "changed_placement":
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE placements SET last_seen_at=last_seen_at+1 "
                "WHERE placement_id=?", (args["placement_id"],),
            )
    elif tamper == "same_sha_new_inode":
        plan = relink(
            store, **{**args, "new_path": original},
            undo_operation=operation,
        )["plan_token"]
        replacement = original.with_name("same-content.pdf")
        replacement.write_bytes(BYTES)
        os.replace(replacement, original)
    else:
        with store._lock, store._connect() as db:
            key = "core.kobo.canonical-placement-relink." + operation
            value = db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()[0]
            db.execute(
                "UPDATE metadata SET value=? WHERE key=?",
                (value[:-1] + ',"schema":"canonical-relink-replayed"}', key),
            )
    before = _read(store, "placements")
    kwargs = {
        **args, "new_path": original, "undo_operation": operation,
    }
    if tamper == "same_sha_new_inode":
        kwargs["confirmation"] = plan
    with pytest.raises(CanonicalRelinkRejected):
        relink(store, **kwargs)
    assert _read(store, "placements") == before
    assert original.read_bytes() == BYTES


def test_offline_cli_undo_is_exact_and_does_not_start_core(
    published, tmp_path, monkeypatch, capsys,
):
    from cifereca.core import kobo_local_reconciliation as repair
    from cifereca import cli

    store, original, moved, args, _source, _finished = published
    profile = _offline(published, tmp_path)
    monkeypatch.setattr(repair, "config_file", lambda **_kw: profile)
    monkeypatch.setattr(repair, "database_file", lambda **_kw: store.path)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: pytest.fail("offline canonical undo may not start Core"),
    )
    before = _unchanged(store)
    common = [
        "kobo-relink-canonical", "--credential", "vault-1",
        "--identity", "A", "--account-id", "buyer-A", "--item", "book-1",
        "--right", "purchase", "--branch", "purchases",
        "--placement", args["placement_id"],
    ]
    original.rename(moved)
    forward = common + ["--path", str(moved)]
    assert cli.main(forward) == 0
    preview = json.loads(capsys.readouterr().out)
    assert cli.main(forward + ["--confirm", preview["plan_token"]]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "relinked"
    assert len(applied["operation_id"]) == 64
    moved.rename(original)
    undo = common + [
        "--path", str(original), "--undo", applied["operation_id"],
    ]
    assert cli.main(undo) == 0
    reverse = json.loads(capsys.readouterr().out)
    assert reverse["status"] == "preview"
    assert cli.main(undo + ["--confirm", reverse["plan_token"]]) == 0
    finished = json.loads(capsys.readouterr().out)
    assert finished["status"] == "relinked"
    assert store.placement_get(args["placement_id"])["path"] == str(original)
    assert _unchanged(store) == before
    assert original.read_bytes() == BYTES
    public = json.dumps(preview) + json.dumps(applied) + json.dumps(reverse) + json.dumps(finished)
    for secret in ("vault-1", "buyer-A", "book-1", str(original), str(moved), SHA):
        assert secret not in public


def test_journal_undo_rejects_intervening_relink_aba_with_original_path_again(
    published,
):
    """A→B→C→B cannot reuse A→B's undo, even in one SQLite second."""
    store, original, moved, args, _source, _finished = published
    unchanged = _unchanged(store)
    original.rename(moved)
    token = relink(store, **args)["plan_token"]
    first = relink(store, **args, confirmation=token)["operation_id"]

    third = moved.with_name("intermediate.pdf")
    moved.rename(third)
    step2 = relink(store, **{**args, "new_path": third})["plan_token"]
    relink(store, **{**args, "new_path": third}, confirmation=step2)
    third.rename(moved)
    step3 = relink(store, **args)["plan_token"]
    relink(store, **args, confirmation=step3)

    # Same SourceReference, origin, SHA and path as immediately after first
    # operation, but another user-controlled operation intervened.
    moved.rename(original)
    before = _read(store, "placements")
    with pytest.raises(
        CanonicalRelinkRejected, match="canonical_relink_plan_changed",
    ):
        relink(
            store, **{**args, "new_path": original},
            undo_operation=first,
        )
    assert _read(store, "placements") == before
    assert _unchanged(store) == unchanged
    assert original.read_bytes() == BYTES


def test_relink_after_exact_undo_reuses_paths_but_never_journal_identity(
    published,
):
    """W4/D5: A→B→A (undo)→B→A (undo) remains usable and unforgeable."""
    store, original, moved, args, _source, finished = published
    immutable = _unchanged(store)
    original.rename(moved)
    first_preview = relink(store, **args)
    first = relink(
        store, **args, confirmation=first_preview["plan_token"],
    )["operation_id"]

    moved.rename(original)
    undo_kwargs = {**args, "new_path": original}
    first_undo_preview = relink(
        store, **undo_kwargs, undo_operation=first,
    )
    first_undo = relink(
        store, **undo_kwargs, undo_operation=first,
        confirmation=first_undo_preview["plan_token"],
    )["operation_id"]
    assert first != first_undo
    assert store.placement_get(finished.placement_id)["path"] == str(original)

    # Another manual move to the SAME target is legitimate. The old
    # path-pair-only operation key would collide with the first journal.
    original.rename(moved)
    redo_preview = relink(store, **args)
    assert redo_preview["plan_token"] != first_preview["plan_token"]
    redo = relink(store, **args, confirmation=redo_preview["plan_token"])
    assert redo["status"] == "relinked"
    second = redo["operation_id"]
    assert len({first, first_undo, second}) == 3
    assert store.placement_get(finished.placement_id)["path"] == str(moved)

    moved.rename(original)
    before = _read(store, "placements")
    # An old undo token cannot revoke the newer B pointer, even when
    # the same old/new path and SHA have returned in a single second.
    with pytest.raises(CanonicalRelinkRejected, match="canonical_relink_plan_changed"):
        relink(store, **undo_kwargs, undo_operation=first)
    assert _read(store, "placements") == before

    second_undo_preview = relink(
        store, **undo_kwargs, undo_operation=second,
    )
    second_undo = relink(
        store, **undo_kwargs, undo_operation=second,
        confirmation=second_undo_preview["plan_token"],
    )
    assert second_undo["status"] == "relinked"
    assert len({first, first_undo, second, second_undo["operation_id"]}) == 4
    assert store.placement_get(finished.placement_id)["path"] == str(original)
    assert _unchanged(store) == immutable
    with store._lock, store._connect() as db:
        rows = db.execute(
            "SELECT key,value FROM metadata WHERE key LIKE "
            "'core.kobo.canonical-placement-relink.%'"
        ).fetchall()
        cursor = db.execute(
            "SELECT value FROM metadata WHERE key LIKE "
            "'core.kobo.canonical-placement-last-operation.v1.%'"
        ).fetchone()
    assert len(rows) == 4
    assert {key.rsplit(".", 1)[1] for key, _ in rows} == {
        first, first_undo, second, second_undo["operation_id"],
    }
    assert cursor == (second_undo["operation_id"],)
    assert original.read_bytes() == BYTES
    assert not moved.exists()
