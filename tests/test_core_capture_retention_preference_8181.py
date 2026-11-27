"""Retention settings are a single persistent Core preference."""
from types import SimpleNamespace
import pytest

from cifereca.brokers import DiagnosticsBrokerFactory
from cifereca.config import Config
from cifereca.core.capture_vault import VaultError
from cifereca.http_trace import preserve_http_capture_preference


def test_retention_settings_are_persistent_and_scoped(tmp_path, monkeypatch):
    from cifereca import paths
    monkeypatch.setattr(paths, "acquisition_lock_file", lambda: tmp_path / "capture.lock")
    cfg = Config(tmp_path / "config.json")
    factory = DiagnosticsBrokerFactory(SimpleNamespace(config=cfg))
    page = factory.for_plugin("ui.page.diagnostics")
    assert not hasattr(factory.for_plugin("provider.fixture"), "set_capture_retention_policy")
    with pytest.raises(VaultError, match="vault_retention_invalid"):
        page.set_capture_retention_policy(0, 1)
    page.set_capture_retention_policy(30, 64 * 1024 * 1024)
    assert page.capture_retention_policy() == {"days": 30, "volume_bytes": 64 * 1024 * 1024}
    reopened = Config(tmp_path / "config.json")
    assert reopened.get("diagnostics.capture_retention_days") == 30
    assert reopened.get("diagnostics.capture_retention_bytes") == 64 * 1024 * 1024
    stale = {"diagnostics": {"capture_retention_days": 90}}
    assert preserve_http_capture_preference(stale, current=cfg)["diagnostics"]["capture_retention_days"] == 30
