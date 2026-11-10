"""Historical fixture builders; v1 backup preview tests removed under #5054."""
from __future__ import annotations

import hashlib
from pathlib import Path


from cifereca.acquisition_engine import Entitlement, SourceIdentity
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


def _item(credential: str, lid: str, variant: str = "a" * 16) -> str:
    scope = hashlib.sha256(credential.encode()).hexdigest()[:20]
    return f"numilog:{scope}:{lid}:{variant}"


def _owned(service, account, lid="123", *, variant="a" * 16,
           trusted=True, downloadable=None):
    item = _item(account.credential_id, lid, variant)
    source = SourceIdentity(
        "numilog", account.credential_id, account.unique_id,
        account.identity_id,
    )
    entitlement_id = Entitlement(source=source, item_id=item).entitlement_id
    metadata = {
        "source": "numilog.authenticated_library" if trusted
        else "numilog.public_catalog",
    }
    if downloadable is not None:
        metadata["downloadable"] = downloadable
    service.storage.entitlement_put({
        "entitlement_id": entitlement_id,
        "provider_id": "numilog", "identity_id": account.identity_id,
        "credential_id": account.credential_id, "account_id": account.unique_id,
        "item_id": item, "entitlement_type": "owned", "state": "active",
        "metadata": metadata,
    })
    return item, entitlement_id


def _broker(service, account):
    broker = SynchronizationBrokerImpl(service)
    return broker, broker._source_id(account)


def _preview(broker, sid, verify=False):
    return broker.preview_local_materialization(
        sid, verify_local_files=verify,
    )


def _ledger(service, account, item, entitlement_id, *, data=b"not-a-real-epub",
            wrong_scope=False, symlink=False):
    artifact_id = "sha256:" + hashlib.sha256(data).hexdigest()
    service.storage.artifact_put({
        "artifact_id": artifact_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data), "media_format": "epub", "kind": "file",
    })
    location = (
        service.config.get("library.directory") + "/"
        + ("alex" if wrong_scope else "hugues")
        + "/numilog/test.epub"
    )
    path = Path(location)
    path.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        external = path.parent.parent.parent.parent / "SECRET.epub"
        external.write_bytes(data)
        path.symlink_to(external)
    else:
        path.write_bytes(data)
    service.storage.placement_put({
        "artifact_id": artifact_id, "path": str(path), "state": "present",
        "identity_id": account.identity_id, "provider_id": "numilog",
        "source_item_id": item,
    })
    service.storage.acquisition_put({
        "acquisition_id": "fictional-acquisition-" + item[-16:],
        "entitlement_id": entitlement_id,
        "provider_id": "numilog", "credential_id": account.credential_id,
        "identity_id": account.identity_id, "account_id": account.unique_id,
        "item_id": item, "artifact_id": artifact_id, "status": "complete",
        "title": "Fictional",
    })
    return path


