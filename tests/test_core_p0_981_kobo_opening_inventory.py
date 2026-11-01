"""#981 — one opening Sync must not fetch a Kobo manifest twice.

Real SQLite, acquisition lock, Core sync and private metadata mirroring;
synthetic provider rows only. The provider's short-lived inventory context
mirrors the Kobo adapter contract tested independently with MockTransport.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cifereca.core import canonical_sync_stage
from tests.test_kobo_backup_guard import _manifest_row, _service


def _instrumented_kobo(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account = session.accounts[0]
    service.accounts = [account]
    state = {"reads": 0, "enters": 0, "exits": 0, "active": False, "rows": None}

    @contextmanager
    def source_sync_inventory(owner):
        assert owner is account
        assert not state["active"]
        state["active"] = True
        state["rows"] = None
        state["enters"] += 1
        try:
            yield
        finally:
            state["active"] = False
            state["rows"] = None
            state["exits"] += 1

    def library():
        if state["active"] and state["rows"] is not None:
            return list(state["rows"])
        state["reads"] += 1
        rows = [session.normalize_item(_manifest_row())]
        if state["active"]:
            state["rows"] = list(rows)
        return rows

    def observe(_service, source, **_kwargs):
        if source.provider_id == "kobo":
            assert state["active"]
            assert source is account
            assert len(session.library()) == 1
        return None

    session.source_sync_inventory = source_sync_inventory
    session.library = library
    monkeypatch.setattr(
        canonical_sync_stage, "observe_canonical_rights_in_sync", observe,
    )
    return service, account, session, state


def test_opening_kobo_canonical_and_private_mirror_share_one_read(tmp_path, monkeypatch):
    service, account, session, state = _instrumented_kobo(tmp_path, monkeypatch)
    try:
        first = service.opening_sync()
        assert first["catalog_mirror"]["status"] in {
            "inventory_only", "partial",
        }
        assert state == {
            "reads": 1, "enters": 1, "exits": 1,
            "active": False, "rows": None,
        }
        second = service.opening_sync()
        assert second["catalog_mirror"]["status"] in {
            "inventory_only", "partial",
        }
        # A second complete opening is not a cache hit from the first.
        assert state == {
            "reads": 2, "enters": 2, "exits": 2,
            "active": False, "rows": None,
        }
    finally:
        service.close()


def test_opening_batch_closes_if_metadata_mirror_fails(tmp_path, monkeypatch):
    service, account, session, state = _instrumented_kobo(tmp_path, monkeypatch)

    def failing_mirror():
        assert state["active"]
        raise RuntimeError("synthetic metadata failure")

    monkeypatch.setattr(service, "mirror_personal_libraries", failing_mirror)
    try:
        result = service.opening_sync()
        assert result["catalog_mirror"]["status"] == "failed"
        assert state["reads"] == 1
        assert state["enters"] == state["exits"] == 1
        assert state["active"] is False and state["rows"] is None
    finally:
        service.close()


def test_opening_changed_binding_never_reuses_inventory_for_metadata(tmp_path, monkeypatch):
    service, account, session, state = _instrumented_kobo(tmp_path, monkeypatch)

    def mutate_after_read(_service, source, **_kwargs):
        assert state["active"] and len(session.library()) == 1
        service._source_context_epoch += 1
        return None

    monkeypatch.setattr(
        canonical_sync_stage, "observe_canonical_rights_in_sync",
        mutate_after_read,
    )
    try:
        result = service.opening_sync()
        assert result["catalog_mirror"]["sources_blocked"] >= 1
        assert result["catalog_mirror"]["notices_mirrored"] == 0
        assert state["reads"] == 1
        assert state["enters"] == state["exits"] == 1
    finally:
        service.close()
