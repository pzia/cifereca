"""P0 #3196: retired Kobo 0.7 startup scheduler is not a Core/UI contract."""
from __future__ import annotations

from pathlib import Path

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca import plugin_api


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_old_kobo_w2_scheduler_and_broker_api_are_absent():
    assert not (ROOT / "core/source_materialization_schedule.py").exists()
    assert not hasattr(SynchronizationBrokerImpl, "local_materialization_schedule")
    assert not hasattr(SynchronizationBrokerImpl, "set_local_materialization_schedule")
    assert not hasattr(plugin_api, "SourceMaterializationScheduleBroker")


def test_no_dead_automatic_kobo_download_ui_is_rendered():
    text = (ROOT / "gui/sync_controller.py").read_text(encoding="utf-8")
    assert "local_materialization_schedule" not in text
    assert "set_local_materialization_schedule" not in text
    assert "Téléchargement automatique de ce compte" not in text
    assert "prepare_local_materialization" in text


def test_canonical_inventory_and_source_consent_stay_available():
    assert hasattr(SynchronizationBrokerImpl, "inventory_refresh_schedule")
    assert hasattr(SynchronizationBrokerImpl, "set_inventory_refresh_schedule")
    assert hasattr(SynchronizationBrokerImpl, "set_source_sync_consent")
