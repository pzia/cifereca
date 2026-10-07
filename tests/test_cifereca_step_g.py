from __future__ import annotations

from pathlib import Path

from cifereca.config import Config
from cifereca.core import CiferecaCore
from cifereca.services import LibraryService

ROOT = Path(__file__).resolve().parents[1]


def test_g_core_contains_no_concrete_provider_implementations_or_internal_adept_adapter():
    assert not (ROOT / "src" / "cifereca" / "providers" / "plugins").exists()
    assert not (ROOT / "src" / "cifereca" / "kobo" / "adept.py").exists()
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src" / "cifereca").rglob("*.py"))
    assert "cifereca.providers.plugins" not in source
    assert "class KoboAdeptAdapter" not in source


def test_g_core_exposes_no_brand_specific_reader_driver_api():
    forbidden = {
        "kobo_devices", "bind_kobo_device", "sync_kobo_device", "kobo_device_bindings",
        "set_kobo_device_desired_state", "kobo_device_preview",
    }
    assert forbidden.isdisjoint(set(dir(LibraryService)))


def test_g_runtime_configuration_has_single_plugin_source_of_truth():
    defaults = (ROOT / "src" / "cifereca" / "config_defaults.py").read_text(encoding="utf-8")
    gui = (ROOT / "src" / "cifereca" / "gui" / "main_window.py").read_text(encoding="utf-8")
    cli = (ROOT / "src" / "cifereca" / "cli.py").read_text(encoding="utf-8")
    executor = (ROOT / "src" / "cifereca" / "kobo" / "executor.py").read_text(encoding="utf-8")
    for token in ("kobo_adept.command", "kobo_adept.update_command", "kobo_adept.run_hook", "kobo_adept.adept_profile"):
        assert token not in defaults
        assert token not in gui
        assert token not in executor
    for command in ("kobo-config-paths", "kobo-config-explain", "kobo-update-status"):
        assert command not in cli


def test_g_fresh_core_is_useful_without_concrete_provider_plugins(tmp_path):
    cfg = tmp_path / "config.json"
    db = tmp_path / "library.sqlite3"
    with CiferecaCore.open(config_path=cfg, database_path=db) as core:
        assert core.library.registry.descriptors() == []
        state = core.library.connect_configured()
        assert state["connected"] == []
        assert core.library.current_account.provider_id == "local"
        assert core.library.current_identity is not None


def test_g_ade_profile_binding_remains_identity_data_not_runtime_configuration(tmp_path):
    config = Config(tmp_path / "config.json")
    config.set("identities", [{
        "id": "alice", "label": "Alice", "directory_name": "alice", "adept_profile": "profile-stable-alice",
        "preferences": {"prefer_original_language": False, "reading_languages": ["fr"]},
    }])
    config.save()
    with CiferecaCore.open(config_path=config.path, database_path=tmp_path / "library.sqlite3") as core:
        assert core.storage.adept_profile_binding_get("identity", "alice") == "profile-stable-alice"
        assert core.config.get("kobo_adept", None) is None
