"""Provider network endpoints belong to plugin descriptors, not Core defaults."""
import json

from cifereca.config import Config
from cifereca.config_defaults import DEFAULT_CONFIG


def test_new_core_config_has_no_supplier_urls(tmp_path):
    config = Config(tmp_path / "new-config.json")
    assert DEFAULT_CONFIG["base_url"] == ""
    assert config.paris_base_url() == ""
    rows = config.provider_accounts("gallica")
    assert len(rows) == 1
    assert rows[0]["options"] == {}
    assert "https://" not in json.dumps(config.data)


def test_legacy_paris_endpoint_is_preserved_not_synthesized(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "username": "synthetic-reader",
        "base_url": "https://mock-paris.example.org",
        "providers": {"accounts": [], "gallica_migration_031": True},
    }), encoding="utf-8")
    config = Config(path)
    paris = config.provider_accounts("paris")
    assert len(paris) == 1
    assert paris[0]["options"] == {
        "base_url": "https://mock-paris.example.org",
    }
    assert config.paris_base_url() == "https://mock-paris.example.org"


def test_legacy_paris_with_no_endpoint_defers_to_plugin(tmp_path):
    path = tmp_path / "legacy-empty.json"
    path.write_text(json.dumps({
        "username": "synthetic-reader",
        "providers": {"accounts": [], "gallica_migration_031": True},
    }), encoding="utf-8")
    config = Config(path)
    assert config.provider_accounts("paris")[0]["options"] == {}
    assert config.paris_base_url() == ""
