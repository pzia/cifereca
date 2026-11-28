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
    finally:
        vault.close()
