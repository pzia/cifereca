"""Synthetic Numilog entitlement/EPUB fixture for read-only inspection tests.

No retired manual placement writer or provider network action is invoked.
"""
from __future__ import annotations

import pytest

from cifereca.models import Account
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_sync_state_contract import _service
from tests.test_library_reconciliation import epub


@pytest.fixture
def rig(tmp_path):
    service = _service(tmp_path)
    root = tmp_path / "EBooks" / "hugues"
    root.mkdir(parents=True, exist_ok=True)
    book = root / "PRIVATE_edition.epub"
    epub(book, title="PRIVATE_TITLE")
    observation, = scan_identity_roots({"default": (root,)}).files
    assert observation.sha256 and observation.structure_validation == "epub_structure_crc_ok"
    account = Account(
        display_name="PRIVATE Numilog", unique_id="PRIVATE-account",
        provider_id="numilog", credential_id="private-credential",
        provider_label="Numilog", identity_id="default",
    )
    service.accounts = [account]
    service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "numilog",
        "credential_id": account.credential_id, "label": "Numilog",
    })
    service.storage.entitlement_put({
        "entitlement_id": "private-right", "provider_id": "numilog",
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "identity_id": "default", "item_id": "private-item",
        "entitlement_type": "owned", "state": "active",
        "metadata": {"source": "numilog.authenticated_library",
                     "title": "PRIVATE_TITLE"},
    })
    service.storage.artifact_put({
        "artifact_id": "sha256:" + observation.sha256,
        "sha256": observation.sha256, "size": observation.size,
        "media_format": "epub", "metadata": {},
    })
    service.storage.acquisition_put({
        "acquisition_id": "private-acquisition",
        "entitlement_id": "private-right", "provider_id": "numilog",
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "identity_id": "default", "item_id": "private-item",
        "title": "PRIVATE_TITLE", "status": "complete",
        "artifact_id": "sha256:" + observation.sha256,
    })
    params = {
        "observation": observation, "identity_id": "default",
        "credential_id": account.credential_id,
        "account_id": account.unique_id, "item_id": "private-item",
    }
    try:
        yield service, book, params
    finally:
        service.close()


def _rows(service, table):
    with service.storage._lock, service.storage._connect() as db:
        return list(db.execute(f"SELECT * FROM {table} ORDER BY 1"))


