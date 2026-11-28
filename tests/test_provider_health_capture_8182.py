from cifereca.core.provider_health_projection import PassiveHealth, Scope, Observation, Outcome
from cifereca.core.capture_vault_service import CoreCaptureVault
from cifereca.plugin_api.capture_vault import CaptureScope


def test_explicit_exact_capture_link(tmp_path):
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        health = PassiveHealth()
        health.set_enabled(True)
        lease = health.lease()
        scope = Scope("fixture", "instance", "account", "owner", "search")
        exact = CaptureScope("fixture", "instance", "account", "owner")
        health.observe(scope, Observation(Outcome.AUTH, "1", "now", "operation:1"), lease=lease)
        writer = port.begin(exact, "operation:1", consented=True)
        writer.save(b"fixture", "completed")
        args = dict(lease=lease, port=port)
        assert not health.link_capture(scope, "operation:1", writer.capture_id, **args)
        assert health.link_capture(scope, "operation:1", writer.capture_id, consented=True, **args)
        assert health.snapshot(scope)[0].capture_id == writer.capture_id
        assert not health.link_capture(scope, "operation:2", writer.capture_id, consented=True, **args)
        other = Scope("fixture", "instance", "other", "owner", "search")
        health.observe(other, Observation(Outcome.AUTH, "1", "now", "operation:1"), lease=lease)
        assert not health.link_capture(other, "operation:1", writer.capture_id, consented=True, **args)
        health.set_enabled(False)
        assert not health.link_capture(scope, "operation:1", writer.capture_id, consented=True, **args)
    finally:
        vault.close()


def test_targeted_archive_rejects_unbound_reference(tmp_path):
    import pytest
    from cifereca.plugin_api.capture_vault import VaultError
    health = PassiveHealth()
    health.set_enabled(True)
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True, health=health)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        exact = CaptureScope("fixture", "instance", "account", "owner")
        with pytest.raises(VaultError, match="vault_confirmation_required"):
            port.archive_search_trace(exact, "unbound")
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.archive_search_trace(exact, "unbound", consented=True)
        assert not (tmp_path / "vault").exists()
    finally:
        vault.close()
