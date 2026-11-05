"""P1 #3416 — authoritative per-source terminal cycle counts, no log inference.

Real SQLite and production SynchronizationBrokerImpl, synthetic providers.
No supplier I/O, acquisition, entitlement write, consent mutation or GTK event.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cifereca.core.diagnostic_source_sync import CANONICAL_EXECUTION_STATUSES
from cifereca.gui.sync_view import (
    sync_source_last_cycle_summary, sync_source_presentation,
)
from cifereca.plugin_api import SyncSourceCycleReceipt
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker


def _execution(**changes):
    data = {state: 0 for state in CANONICAL_EXECUTION_STATUSES}
    data.update(changes)
    return data


def _cycle(broker):
    return broker.sources()[0].last_cycle


def test_partial_cycle_counts_persist_atomically_across_sqlite_restart(tmp_path):
    filename = tmp_path / "p1-source-cycle.sqlite3"
    db = Storage(filename)
    try:
        broker = fake_broker(db)
        sid = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "partial", "loans": 522,
            "canonical_execution": _execution(
                present_attested=512, pending=7, failed=3,
            ),
        }
        result = broker.synchronize_source(sid)
        assert result.ok is False
        expected = _execution(present_attested=512, pending=7, failed=3)
        assert result.facts["canonical_execution"] == expected
        receipt = _cycle(broker)
        assert type(receipt) is SyncSourceCycleReceipt
        assert receipt.status == "partial" and receipt.mode == "manual"
        assert dict(receipt.canonical_execution) == expected
        raw = json.loads(db.metadata_get("sync.source.state." + sid))
        assert raw["status"] == "partial"
        assert raw["last_cycle"]["status"] == "partial"
        assert raw["last_cycle"]["canonical_execution"] == expected
        assert raw["last_cycle"]["attempted_at"] == raw["last_attempt"]
        rendered = sync_source_presentation(broker.sources()[0])
        assert "Synchronisation : Partielle" in rendered.summary
        assert "présents attestés 512" in rendered.summary
        assert "téléchargés 0" in rendered.summary
        assert "en attente 7" in rendered.summary
        assert "échecs 3" in rendered.summary
    finally:
        db.close()
    reloaded = Storage(filename)
    try:
        receipt = _cycle(fake_broker(reloaded))
        assert receipt.status == "partial"
        assert dict(receipt.canonical_execution) == expected
    finally:
        reloaded.close()


def test_no_canonical_envelope_is_unknown_not_zero_even_when_source_succeeded(tmp_path):
    db = Storage(tmp_path / "unknown.sqlite3")
    try:
        broker = fake_broker(db)
        sid = broker.sources()[0].source_id
        result = broker.synchronize_source(sid)  # Default returns only loans=3.
        assert result.ok
        receipt = _cycle(broker)
        assert receipt.status == "success"
        assert receipt.canonical_execution is None
        summary = sync_source_last_cycle_summary(broker.sources()[0])
        assert "Résultats de réception locale : non attestés" in summary
        assert "téléchargés 0" not in summary
        # Explicit *complete* all-zero Core envelope is different from no data.
        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "success", "canonical_execution": _execution(),
        }
        assert broker.synchronize_source(sid).ok
        assert dict(_cycle(broker).canonical_execution) == _execution()
        assert "téléchargés 0" in sync_source_last_cycle_summary(broker.sources()[0])
    finally:
        db.close()


@pytest.mark.parametrize("bad", [
    {"pending": 1},
    {**_execution(), "failed": True},
    {**_execution(), "pending": -1},
    {**_execution(), "failed": 1_000_001},
    {**_execution(), "private": "PRIVATE_TOKEN"},
    "PRIVATE_TOKEN",
    None,
])
def test_incomplete_bad_execution_never_persists_fake_counts(tmp_path, bad):
    db = Storage(tmp_path / "bad.sqlite3")
    try:
        broker = fake_broker(db)
        sid = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "partial", "canonical_execution": bad,
        }
        result = broker.synchronize_source(sid)
        assert "canonical_execution" not in result.facts
        assert _cycle(broker).status == "partial"
        assert _cycle(broker).canonical_execution is None
        assert "PRIVATE_TOKEN" not in db.metadata_get("sync.source.state." + sid)
        assert "non attestés" in sync_source_last_cycle_summary(broker.sources()[0])
    finally:
        db.close()


def test_failed_next_cycle_clears_previous_counts_but_keeps_last_success(tmp_path):
    db = Storage(tmp_path / "failed.sqlite3")
    try:
        broker = fake_broker(db)
        sid = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "success",
            "canonical_execution": _execution(downloaded=6),
        }
        assert broker.synchronize_source(sid).ok
        prior_success = broker.sources()[0].last_success

        def fail(*_args, **_kw):
            raise RuntimeError("PRIVATE_TOKEN")

        broker.service.synchronize_account_source = fail
        assert not broker.synchronize_source(sid).ok
        source = broker.sources()[0]
        assert source.last_success == prior_success
        assert source.status == "error"
        assert source.last_cycle.status == "failed"
        assert source.last_cycle.canonical_execution is None
        assert "téléchargés 6" not in sync_source_last_cycle_summary(source)
        assert "PRIVATE_TOKEN" not in db.metadata_get("sync.source.state." + sid)
    finally:
        db.close()


def test_two_exact_borrowers_do_not_share_a_cycle_even_on_same_provider(tmp_path):
    db = Storage(tmp_path / "accounts.sqlite3")
    try:
        first = fake_broker(db)
        second = fake_broker(db)
        b = second.service.accounts[0]
        b.unique_id = "borrower-2"
        b.credential_id = "paris-2"
        second.service._sessions = {
            "paris::paris-2": second.service._sessions["paris::paris-1"]
        }
        first_sid = first.sources()[0].source_id
        second_sid = second.sources()[0].source_id
        assert first_sid != second_sid
        first.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "partial",
            "canonical_execution": _execution(pending=8),
        }
        assert not first.synchronize_source(first_sid).ok
        assert second.sources()[0].last_cycle is None
        second.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "success",
            "canonical_execution": _execution(downloaded=6),
        }
        assert second.synchronize_source(second_sid).ok
        assert dict(first.sources()[0].last_cycle.canonical_execution)["pending"] == 8
        assert dict(second.sources()[0].last_cycle.canonical_execution)["downloaded"] == 6
    finally:
        db.close()


def test_corrupted_persisted_cycle_does_not_reach_public_source(tmp_path):
    db = Storage(tmp_path / "forged.sqlite3")
    try:
        broker = fake_broker(db)
        sid = broker.sources()[0].source_id
        key = "sync.source.state." + sid
        valid = {
            "schema": 1, "status": "partial", "mode": "manual",
            "attempted_at": "2026-11-05T03:00:00+00:00",
            "canonical_execution": _execution(pending=8),
        }
        for invalid in (
            {**valid, "canonical_execution": {**_execution(), "failed": True}},
            {**valid, "canonical_execution": {**_execution(), "url": "PRIVATE_TOKEN"}},
            {**valid, "schema": True},
            {**valid, "status": "PRIVATE_TOKEN"},
            {**valid, "mode": "auto"},
            {**valid, "attempted_at": "PRIVATE_TOKEN"},
            {**valid, "extra": "PRIVATE_TOKEN"},
        ):
            db.metadata_set(key, json.dumps({"status": "partial", "last_cycle": invalid}))
            source = broker.sources()[0]
            assert source.last_cycle is None
            assert "PRIVATE_TOKEN" not in sync_source_presentation(source).summary
        db.metadata_set(key, json.dumps({"status": "partial", "last_cycle": valid}))
        assert dict(_cycle(broker).canonical_execution)["pending"] == 8
    finally:
        db.close()


def test_ui_never_projects_forged_cycle_or_counts(tmp_path):
    db = Storage(tmp_path / "render.sqlite3")
    try:
        broker = fake_broker(db)
        source = broker.sources()[0]
        receipt = SyncSourceCycleReceipt(
            "partial", "2026-11-05T03:00:00+00:00", "manual",
            tuple(_execution(pending=2).items()),
        )
        assert "en attente 2" in sync_source_last_cycle_summary(
            replace(source, last_cycle=receipt)
        )
        for corrupted in (
            tuple({**_execution(), "pending": True}.items()),
            tuple({"pending": 3}.items()),
            tuple({**_execution(), "token": "PRIVATE_TOKEN"}.items()),
        ):
            shown = sync_source_last_cycle_summary(
                replace(source, last_cycle=replace(receipt, canonical_execution=corrupted)),
            )
            assert "non attestés" in shown
            assert "PRIVATE_TOKEN" not in shown
        assert sync_source_last_cycle_summary(
            replace(source, last_cycle=replace(receipt, mode="PRIVATE_TOKEN"))
        ) == ""
    finally:
        db.close()


def test_colliding_primary_and_literal_account_ids_cannot_share_cycle_counts(tmp_path):
    from cifereca.models import Account

    db = Storage(tmp_path / "collision.sqlite3")
    try:
        broker = fake_broker(db)
        primary = broker.service.accounts[0]
        primary.unique_id = ""
        literal = Account(
            identity_id=primary.identity_id,
            provider_id=primary.provider_id,
            credential_id=primary.credential_id,
            unique_id="paris:paris-1:primary",
            display_name="Other borrower",
        )
        broker.service.accounts.append(literal)
        # fake_broker's helper normally returns only its initial account.
        broker.service.accounts_for_identity = (
            lambda identity=None: [
                row for row in broker.service.accounts
                if identity is None or row.identity_id == identity
            ]
        )
        assert broker._source_id(primary) == broker._source_id(literal)
        sid = broker._source_id(primary)
        db.metadata_set("sync.source.state." + sid, json.dumps({
            "status": "partial",
            "last_cycle": {
                "schema": 1, "mode": "manual", "status": "partial",
                "attempted_at": "2026-11-05T03:00:00+00:00",
                "canonical_execution": _execution(pending=8),
            },
        }))
        references = broker.sources()
        assert len(references) == 2
        assert all(source.enabled is False for source in references)
        assert all(source.last_cycle is None for source in references)
        assert not any("en attente 8" in sync_source_presentation(source).summary
                       for source in references)
    finally:
        db.close()


def test_declared_failed_core_outcome_is_not_relabelled_partial_in_cycle(tmp_path):
    db = Storage(tmp_path / "failed-result.sqlite3")
    try:
        broker = fake_broker(db)
        sid = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *_a, **_kw: {
            "status": "failed",
            "canonical_execution": _execution(failed=3, pending=2),
        }
        result = broker.synchronize_source(sid)
        assert result.ok is False
        assert result.facts["status"] == "failed"
        terminal = broker.sources()[0].last_cycle
        assert terminal.status == "failed"
        assert dict(terminal.canonical_execution)["failed"] == 3
        text = sync_source_last_cycle_summary(broker.sources()[0])
        assert " : Échec" in text and "échecs 3" in text
    finally:
        db.close()
