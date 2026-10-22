"""#2714-C3c: undo only a journaled Numilog placement, atomically."""
from __future__ import annotations

import json
import sqlite3

import pytest

from cifereca.core.numilog_manual_link_transaction import (
    ManualAttachmentRejected, reconcile_numilog_manual, undo_numilog_manual,
)
from cifereca.core.library_fresh_file_check import FreshFileCheck
from tests.test_core_p0_2714_numilog_manual_transaction import (
    rig, _rows, _stage,
)


def _attach(rig):
    plan = _stage(rig)
    done = reconcile_numilog_manual(
        rig[0], **rig[2], confirmation=plan["plan_token"],
    )
    assert done["status"] == "applied"
    assert len(done["operation_id"]) == 64
    assert "PRIVATE" not in json.dumps(done)
    return done


def _undo(rig, done, **kw):
    return undo_numilog_manual(
        rig[0], **{**rig[2], **kw, "operation_id": done["operation_id"]},
    )


def test_undo_preview_is_readonly_then_confirmation_deletes_only_own_placement(rig):
    service, book, _ = rig
    bytes_before = book.read_bytes()
    before = {
        table: _rows(service, table)
        for table in ("entitlements", "artifacts", "acquisition_ledger", "catalog_entries")
    }
    done = _attach(rig)
    assert len(_rows(service, "placements")) == 1
    plan = _undo(rig, done)
    assert plan["status"] == "undo_preview"
    assert plan["requires_explicit_confirmation"] and plan["dry_run"]
    assert plan["read_only"] and not plan["changed"]
    assert len(_rows(service, "placements")) == 1
    assert "PRIVATE" not in json.dumps(plan)
    removed = _undo(rig, done, confirmation=plan["plan_token"])
    assert removed["status"] == "undone" and removed["changed"] == 1
    assert removed["file_mutations"] == removed["rights_mutations"] == 0
    assert book.read_bytes() == bytes_before
    assert _rows(service, "placements") == []
    for table, expected in before.items():
        assert _rows(service, table) == expected
    assert _undo(rig, done)["status"] == "already_undone"


def test_undo_requires_own_journal_and_matching_snapshot(rig):
    done = _attach(rig)
    before = _rows(rig[0], "placements")
    for invalid in ("0" * 64, "PRIVATE", "a" * 63):
        with pytest.raises(ManualAttachmentRejected):
            _undo(rig, {"operation_id": invalid})
    assert _rows(rig[0], "placements") == before


def test_undo_token_invalidated_by_changed_state_before_confirmation(rig):
    done = _attach(rig)
    plan = _undo(rig, done)
    with rig[0].storage._lock, rig[0].storage._connect() as db:
        db.execute("UPDATE acquisition_ledger SET updated_at=updated_at+1")
    before = _rows(rig[0], "placements")
    with pytest.raises(ManualAttachmentRejected, match="^journal_evidence_changed$"):
        _undo(rig, done, confirmation=plan["plan_token"])
    assert _rows(rig[0], "placements") == before


def test_undo_refuses_foreign_edit_of_placement_before_preview(rig):
    done = _attach(rig)
    with rig[0].storage._lock, rig[0].storage._connect() as db:
        db.execute("UPDATE placements SET provider_id='kobo'")
    before = _rows(rig[0], "placements")
    with pytest.raises(ManualAttachmentRejected, match="^(placement_conflict|journal_state_conflict)$"):
        _undo(rig, done)
    assert _rows(rig[0], "placements") == before


def test_undo_failure_after_delete_rolls_back_both_metadata_and_placement(
    rig, monkeypatch,
):
    from cifereca.core import numilog_manual_link_transaction as engine

    done = _attach(rig)
    plan = _undo(rig, done)
    before = _rows(rig[0], "placements")
    originals = _rows(rig[0], "metadata")
    original = engine.revalidate_observed_file
    calls = [0]

    def mutate_before_commit(*args, **kwargs):
        calls[0] += 1
        return (original(*args, **kwargs) if calls[0] == 1
                else FreshFileCheck(False, "file_changed"))

    monkeypatch.setattr(engine, "revalidate_observed_file", mutate_before_commit)
    with pytest.raises(ManualAttachmentRejected, match="^file_changed_before_commit$"):
        _undo(rig, done, confirmation=plan["plan_token"])
    assert calls[0] == 2
    assert _rows(rig[0], "placements") == before
    assert _rows(rig[0], "metadata") == originals


def test_undo_rejects_corrupted_journal_without_echoing_private_bytes(rig):
    done = _attach(rig)
    with rig[0].storage._lock, rig[0].storage._connect() as db:
        db.execute("UPDATE metadata SET value=? WHERE key=?",
                   (json.dumps({"PRIVATE": "secret"}), 
                    "core.numilog.local-attachment." + done["operation_id"]))
    before = _rows(rig[0], "placements")
    with pytest.raises(ManualAttachmentRejected, match="^journal_invalid$") as error:
        _undo(rig, done)
    assert "PRIVATE" not in str(error.value)
    assert _rows(rig[0], "placements") == before


def test_undo_confirmation_cannot_replay_other_accounts_or_changed_file(rig):
    done = _attach(rig)
    plan = _undo(rig, done)
    with pytest.raises(ManualAttachmentRejected):
        _undo(rig, done, account_id="other-account",
              confirmation=plan["plan_token"])
    assert len(_rows(rig[0], "placements")) == 1
    rig[1].write_bytes(rig[1].read_bytes() + b"mutated")
    with pytest.raises(ManualAttachmentRejected, match="^file_revalidation_failed$"):
        _undo(rig, done, confirmation=plan["plan_token"])
    assert len(_rows(rig[0], "placements")) == 1


@pytest.mark.parametrize("stage", ["before_preview", "after_preview"])
def test_undo_cannot_replay_earlier_account_context_epoch(rig, stage):
    """Same strings after reconnect do not authorize prior account journal."""
    service, book, _options = rig
    original_file = book.read_bytes()
    done = _attach(rig)
    before = _rows(service, "placements")
    original_metadata = _rows(service, "metadata")
    plan = _undo(rig, done) if stage == "after_preview" else None

    # Simulates logout/reconnect/rebind of the same textual account. The
    # present scope is otherwise identical; only the account epoch changes.
    service._account_context_epoch += 1
    with pytest.raises(ManualAttachmentRejected, match="^journal_scope_changed$"):
        _undo(
            rig, done,
            **({"confirmation": plan["plan_token"]} if plan else {}),
        )
    assert _rows(service, "placements") == before
    assert _rows(service, "metadata") == original_metadata
    assert book.read_bytes() == original_file


def test_undo_refuses_catalogue_reference_overflow_before_any_delete(rig):
    """A hidden fourth catalogue reference cannot be orphaned by an undo."""
    from tests.test_core_p0_2714_numilog_manual_transaction import _insert_catalogue_refs

    done = _attach(rig)
    service, book, _ = rig
    placement_id = service.storage.placement_for_path(book)["placement_id"]
    _insert_catalogue_refs(service, placement_id, 4, foreign_last=True)
    before = _rows(service, "placements")
    journal_before = _rows(service, "metadata")
    with pytest.raises(ManualAttachmentRejected, match="^catalogue_evidence_ambiguous$"):
        _undo(rig, done)
    assert _rows(service, "placements") == before
    assert _rows(service, "metadata") == journal_before
