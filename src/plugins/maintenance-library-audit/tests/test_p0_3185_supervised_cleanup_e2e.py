"""#3185: confirmed Maintenance tool invokes real Core B dedup without rights loss.

This is the POST-CONFIRMATION tool.run path. All data are synthetic; there is
no GTK fake click, no provider network, and no license/DRM activity.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.source_convergence import SourceKey
from cifereca.plugin_api import LibraryMutationRequest, MaintenancePlan
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca.storage import Storage
from cifereca_library_audits import DuplicateCleanupTool


BYTES = b"%PDF-1.7\nSynthetic completed publication\n"
SHA = hashlib.sha256(BYTES).hexdigest()
STAMP = "2026-10-28T08:00:00+00:00"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "library.sqlite")
    root = tmp_path / "EBooks"
    paths, publications = [], []
    for identity, provider, credential, account, name in (
        ("A", "seller-a", "card-a", "buyer-a", "first.pdf"),
        ("A", "seller-b", "card-b", "buyer-b", "second.pdf"),
        ("B", "seller-c", "card-c", "buyer-c", "foreign.pdf"),
    ):
        storage.identity_put({
            "identity_id": identity, "directory_name": identity, "label": identity,
        })
        connection = storage.provider_connection_put({
            "identity_id": identity, "provider_id": provider,
            "credential_id": credential, "label": "Synthetic",
        })
        storage.provider_account_put({
            "identity_id": identity, "provider_id": provider,
            "credential_id": credential, "connection_id": connection,
            "account_id": account, "label": "Synthetic",
        })
        path = root / identity / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        scope = ProviderRightsScope(identity, provider, credential, account)
        source = SourceKey(scope, "book", "purchase", "purchase", "purchases")
        observed = ProviderRightObservation(
            "book", "purchase", "purchase", "active",
            "provider_response", "positive-right-observation", STAMP, "unknown",
        )
        rights = ProviderRightsSnapshot(
            scope, (observed,), status="partial", coverage="partial",
            branch="purchases",
        )
        published = publish_completed_acquisition(
            storage, library_root=root, path=path,
            source=source, snapshot=rights, final_sha256=SHA,
            delivery_receipt_ref="synthetic-completed-delivery",
        )
        paths.append(path)
        publications.append(published)
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    try:
        yield storage, root, paths, publications, lock_path
    finally:
        storage.close()


def rows(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            db.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("artifacts", "placements", "source_references", "origin_links")
        )


def backup(rig):
    storage, _root, paths, _pubs, _lock = rig
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "title,local_path,status,updated_at,identity_id,account_id,"
            "sha256,size) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("seller-b", "card-b", "book", "Synthetic", str(paths[1]),
             "complete", 1, "A", "buyer-b", SHA, len(BYTES)),
        )


class _Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **kwargs):
        self.events.append(kwargs)


class _Context:
    def __init__(self, broker):
        self._broker = broker

    def broker(self, name):
        assert name == "library.maintenance"
        return self._broker


def _setup(rig, monkeypatch):
    storage, root, _paths, pubs, lock_path = rig
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: lock_path)
    service = SimpleNamespace(
        storage=storage, config=SimpleNamespace(
            get=lambda key, default=None: (
                str(root) if key == "library.directory" else default
            ),
        ),
    )
    broker = LibraryMaintenanceBrokerImpl(service)
    tool = DuplicateCleanupTool().activate(_Context(broker))
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="Synthetic, user-approved destructive maintenance action",
        facts={"pairs": ((pubs[1].placement_id, pubs[0].placement_id),)},
    )
    return broker, tool, plan


def test_confirmed_gtk_maintenance_contract_uses_core_supervisor_e2e(
    rig, monkeypatch,
):
    storage, _root, paths, pubs, _lock = rig
    backup(rig)
    broker, tool, plan = _setup(rig, monkeypatch)
    before = rows(storage)
    control = _Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0}
    assert before == (1, 3, 3, 3)
    assert rows(storage) == (1, 2, 3, 3)
    assert storage.placement_get(pubs[1].placement_id) is None
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT local_path FROM backup_items WHERE credential_id='card-b'"
        ).fetchone() == (str(paths[0]),)
    assert all(str(paths[1]) not in str(event) for event in control.events)
    assert all(str(paths[1]) not in str(text) for text in result.warnings)


def test_tool_reports_pending_without_automatic_recovery_after_fs_denial(
    rig, monkeypatch,
):
    storage, _root, paths, pubs, _lock = rig
    broker, tool, plan = _setup(rig, monkeypatch)
    actual_unlink = Path.unlink

    def deny(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic-unlink-refused")
        return actual_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        failed = tool.run(plan, _Control())
    assert not failed.ok and failed.changed == 0
    assert failed.facts["failures"] == 1
    assert paths[1].read_bytes() == BYTES
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert rows(storage) == (1, 3, 3, 3)
    # The tool does not silently re-attempt unlink or infer consent for
    # recovery; only a fresh explicit confirmed Core request can finish it.
    recovery = broker.run(broker.plan(LibraryMutationRequest(
        "canonical_dedup", placement_id=pubs[1].placement_id,
        keep_placement_id=pubs[0].placement_id,
        options={"action": "recover", "confirmed": True},
    )))
    assert recovery.ok and recovery.facts["status"] == "retired"
    assert not paths[1].exists()
    assert rows(storage) == (1, 2, 3, 3)
