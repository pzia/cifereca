"""P0 #5054: preserve trustworthy offline indexing of pre-cutover receipts.

NO synthetic test calls the retired direct writer: historical receipts and
bytes are deliberately supplied as old local evidence, never as supplier
download permission.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace as NS
import zipfile

import pytest

from cifereca.acquisition_engine import stable_id
from cifereca.providers import ProviderConfigurationError
from tests.test_sync_state_contract import _service
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row


@pytest.fixture
def historical(tmp_path):
    service = _service(tmp_path)
    account, session, _inventory = _connect(service, number=1)
    item = _row(account.credential_id, 0)
    service.storage.entitlement_put({
        "entitlement_id": "legacy-right",
        "provider_id": "numilog", "identity_id": account.identity_id,
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "item_id": item.record_id, "entitlement_type": "owned",
        "state": "active",
        "metadata": {"source": "numilog.authenticated_library"},
    })
    target_dir = service.library_identity_provider_dir(account.identity_id, "numilog")
    path = Path(target_dir) / "historical.epub"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", "<html><body>Historical book</body></html>")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    acquisition_id = stable_id(
        "numilog-materialization", account.identity_id,
        account.credential_id, account.unique_id, item.record_id,
    )
    service.storage.acquisition_put({
        "acquisition_id": acquisition_id,
        "provider_id": "numilog", "identity_id": account.identity_id,
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "item_id": item.record_id,
        "status": "attention_required",
        "attention_code": "numilog_index_pending",
        "metadata": {
            "kind": "numilog_delivered_artifact_v1",
            "phase": "index_pending", "path": str(path.absolute()),
            "root": str(Path(target_dir).absolute()),
            "sha256": digest, "size": path.stat().st_size,
        },
    })
    session.download_direct = lambda *_a, **_kw: pytest.fail("offline cannot GET")
    try:
        yield NS(service=service, session=session, account=account, item=item,
                 path=path, receipt=acquisition_id)
    finally:
        service.close()


def test_valid_pre_cutover_bytes_can_index_offline_without_get(historical):
    receipt = historical.service.storage.acquisition_get(historical.receipt)
    assert receipt["status"] == "attention_required"
    result = historical.service.resume_numilog_materialization(historical.receipt)
    assert result.path == historical.path
    assert result.indexed["status"] == "indexed"
    assert result.identity_id == historical.account.identity_id
    after = historical.service.storage.acquisition_get(historical.receipt)
    assert after["status"] == "complete"
    assert after["metadata"]["phase"] == "indexed"
    assert after["artifact_id"] == result.artifact_id


@pytest.mark.parametrize("damage", ["changed", "absent", "symlink", "wrong_identity", "foreign_ledger"])
def test_changed_or_ambiguous_historical_proof_never_indexes(
    historical, monkeypatch, tmp_path, damage,
):
    if damage == "changed":
        historical.path.write_bytes(b"foreign")
    elif damage == "absent":
        historical.path.unlink()
    elif damage == "symlink":
        outside = tmp_path / "other.epub"
        outside.write_bytes(historical.path.read_bytes())
        historical.path.unlink()
        historical.path.symlink_to(outside)
    elif damage == "wrong_identity":
        historical.service.current_identity = NS(identity_id="other")
    elif damage == "foreign_ledger":
        row = historical.service.storage.acquisition_get(historical.receipt)
        historical.service.storage.acquisition_put({
            **row, "acquisition_id": "second-legacy-receipt",
        })
    called = []
    monkeypatch.setattr(historical.service, "catalog_index_path",
                        lambda *_a, **_kw: called.append(True))
    with pytest.raises(ProviderConfigurationError):
        historical.service.resume_numilog_materialization(historical.receipt)
    assert called == []
    assert historical.service.storage.acquisition_get(historical.receipt)["status"] != "complete"
