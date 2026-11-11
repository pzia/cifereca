"""P0 #5054: startup pseudo-consent is retired, canonical source control remains."""
from pathlib import Path
import ast

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.models import Account
from tests.test_sync_state_contract import _service


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_numilog_startup_consent_module_and_inert_gtk_switch_are_absent():
    assert not (ROOT / "core/numilog_materialization_consent.py").exists()
    broker = (ROOT / "brokers.py").read_text(encoding="utf-8")
    gui = (ROOT / "gui/sync_controller.py").read_text(encoding="utf-8")
    storage = (ROOT / "storage.py").read_text(encoding="utf-8")
    ast.parse(broker)
    ast.parse(gui)
    ast.parse(storage)
    assert "_numilog_consent_plans" not in broker
    for marker in (
        "prepare_numilog_materialization_consent",
        "confirm_numilog_materialization_consent",
        "revoke_numilog_materialization_consent",
        "numilog_materialization_schedule",
    ):
        assert marker not in broker
        assert marker not in gui
    assert "Sauvegarde automatique des achats Numilog" not in gui
    for marker in (
        "def numilog_materialization_change_consent(",
        "def numilog_materialization_disable_source(",
        "def numilog_materialization_enable_source(",
    ):
        assert marker not in storage
    assert "def set_source_sync_consent(" in broker
    assert "def set_enabled(" in broker


def test_provider_neutral_source_enable_is_still_reversible(tmp_path):
    service = _service(tmp_path)
    try:
        account = Account(
            display_name="Fixture seller", unique_id="borrower",
            provider_id="fixture-provider", credential_id="fixture-card",
            identity_id="default",
        )
        service.accounts.append(account)
        connection = service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "fixture-provider",
            "credential_id": "fixture-card", "enabled": True,
        })
        service.storage.provider_account_put({
            "connection_id": connection, "identity_id": "default",
            "provider_id": "fixture-provider", "credential_id": "fixture-card",
            "account_id": "borrower", "label": "Fixture",
        })
        broker = SynchronizationBrokerImpl(service)
        source_id = broker._source_id(account)
        broker.set_enabled(source_id, False)
        assert service.storage.metadata_get("sync.source.enabled." + source_id) == "0"
        broker.set_enabled(source_id, True)
        assert service.storage.metadata_get("sync.source.enabled." + source_id) == "1"
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()
