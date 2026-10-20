"""P0 #2024: receipt insertion revalidates placement-only conflicts.

No provider GET, ACSM, download, deletion, or real identity. This simulates
a placement appearing AFTER an earlier eligible SQLite purchase preview.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.numilog_materialization_checkpoint import _record
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_numilog_purchase_preflight import _rig, _check


@pytest.mark.parametrize("owner", ["same", "different"])
@pytest.mark.parametrize("state", ["present", "missing"])
def test_late_local_placement_rejects_pending_receipt_before_sql_write(
    tmp_path, monkeypatch, owner, state,
):
    service, account, session, calls, item_id, entitlement, broker, sid = _rig(tmp_path)
    try:
        _check(
            broker.preflight_numilog_purchase(sid, item_id),
            "candidate_unverified", candidate=True,
        )
        target = tmp_path / "PRIVATE_EXISTING.epub"
        target.write_bytes(b"UNCHANGED")
        service.storage.placement_put({
            "provider_id": "numilog",
            "identity_id": account.identity_id if owner == "same" else "alice",
            "source_item_id": item_id,
            "path": str(target),
            "state": state,
        })
        evidence = service.storage.numilog_purchase_preflight_evidence(
            identity_id=account.identity_id, credential_id=account.credential_id,
            account_id=account.unique_id, item_id=item_id,
        )
        assert evidence["acquisitions"] == []
        assert evidence["placement_conflict"] is True
        attempted_writes = []
        monkeypatch.setattr(
            service.storage, "acquisition_put",
            lambda *args, **kwargs: attempted_writes.append((args, kwargs)),
        )
        item = NS(source_credential=account.credential_id, record_id=item_id)
        with pytest.raises(ProviderConfigurationError) as error:
            _record(
                service, item, tmp_path, account.identity_id,
                account.unique_id, ("0" * 64, 9, str(target)),
            )
        assert attempted_writes == []
        assert service.storage.acquisitions(provider_id="numilog") == []
        assert target.read_bytes() == b"UNCHANGED"
        assert calls == []
        assert "PRIVATE" not in str(error.value)
        assert account.credential_id not in str(error.value)
        assert item_id not in str(error.value)
    finally:
        service.close()


@pytest.mark.parametrize("evidence_value", [None, 0, "False", [], {}])
def test_unattested_placement_proof_cannot_create_receipt(
    tmp_path, monkeypatch, evidence_value,
):
    service, account, session, calls, item_id, entitlement, broker, sid = _rig(tmp_path)
    try:
        real = service.storage.numilog_purchase_preflight_evidence
        def malformed(**kwargs):
            evidence = real(**kwargs)
            assert evidence["placement_conflict"] is False
            return {**evidence, "placement_conflict": evidence_value}
        monkeypatch.setattr(
            service.storage, "numilog_purchase_preflight_evidence", malformed,
        )
        writes = []
        monkeypatch.setattr(
            service.storage, "acquisition_put", lambda *args, **kwargs: writes.append(1),
        )
        with pytest.raises(ProviderConfigurationError):
            _record(
                service, NS(source_credential=account.credential_id, record_id=item_id),
                tmp_path, account.identity_id, account.unique_id,
                ("0" * 64, 9, str(tmp_path / "PRIVATE.epub")),
            )
        assert writes == []
        assert calls == []
    finally:
        service.close()
