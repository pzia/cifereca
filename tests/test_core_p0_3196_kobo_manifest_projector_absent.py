"""#3196: Kobo manifest cannot mint a Core entitlement or be imported."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src/cifereca"


def test_manifest_projector_is_physically_absent() -> None:
    assert not (CORE / "core/kobo_manifest_rights.py").exists()


def test_core_entrypoints_never_import_deleted_manifest_projector() -> None:
    for path in (
        CORE / "services.py",
        CORE / "core/sync_coordination.py",
        CORE / "core/search_owned_evidence.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom)
            and "kobo_manifest_rights" in (node.module or "")
            for node in ast.walk(tree)
        ), str(path)


def test_synthetic_historical_entitlement_is_not_an_acquisition_receipt(tmp_path):
    from cifereca.storage import Storage
    from tests.kobo_persisted_right_fixture import seed_persisted_purchase

    class Target:
        storage = Storage(tmp_path / "ledger.sqlite3")

    target = Target()
    seed_persisted_purchase(
        target, identity_id="alice", credential_id="vault-1",
        account_id="account-1", edition_id="edition-1",
    )
    assert len(target.storage.entitlements(provider_id="kobo")) == 1
    assert target.storage.acquisitions() == []
    assert target.storage.placements() == []
