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


def test_targeted_archive_positive(tmp_path, monkeypatch):
    import json
    from io import BytesIO
    from zipfile import ZipFile
    import httpx
    from cifereca import http_trace
    from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
    trace = http_trace.HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", trace)
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "instance", "account", "owner", "search")
    exact = CaptureScope("fixture", "instance", "account", "owner")
    with diagnostic_task_scope():
        ref = diagnostic_task_reference()
        exchange = trace.begin(httpx.Request("GET", "https://example.invalid/selected"), "fixture")
        exchange.entry["outcome"] = "completed"
    with diagnostic_task_scope():
        other = trace.begin(httpx.Request("GET", "https://example.invalid/other"), "fixture")
        other.entry["outcome"] = "completed"
    health.observe(scope, Observation(Outcome.AUTH, "1", "now", ref), lease=health.lease())
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True, health=health)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        capture_id = port.archive_search_trace(exact, ref, consented=True)
        assert health.snapshot(scope)[0].capture_id == capture_id
        captured = port.read(exact, capture_id)
        with ZipFile(BytesIO(captured.payload)) as archive:
            index = json.loads(archive.read("index.json"))
            assert index["session_ref"] == ref
            assert len(index["exchanges"]) == 1
            assert index["exchanges"][0]["task_ref"] == ref
            assert index["exchanges"][0]["request"]["url"].endswith("/selected")
    finally:
        vault.close()


def test_revoked_archive_rolls_back_only_new_capture(tmp_path, monkeypatch):
    import pytest
    import httpx
    from cifereca import http_trace
    from cifereca.core import capture_vault_service as module
    from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
    from cifereca.plugin_api.capture_vault import VaultError
    trace = http_trace.HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", trace)
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "instance", "account", "owner", "search")
    exact = CaptureScope("fixture", "instance", "account", "owner")
    with diagnostic_task_scope():
        ref = diagnostic_task_reference()
        exchange = trace.begin(httpx.Request("GET", "https://example.invalid/read"), "fixture")
        exchange.entry["outcome"] = "completed"
    health.observe(scope, Observation(Outcome.AUTH, "1", "now", ref), lease=health.lease())
    allowed = [True]
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: allowed[0], health=health)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        old = port.begin(exact, "existing", consented=True)
        old.save(b"existing", "completed")
        save = module._Writer.save
        def revoke_after_save(self, *args, **kwargs):
            save(self, *args, **kwargs)
            allowed[0] = False
        monkeypatch.setattr(module._Writer, "save", revoke_after_save)
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.archive_search_trace(exact, ref, consented=True)
        allowed[0] = True
        assert [row.capture_id for row in port.sessions(exact)] == [old.capture_id]
        assert port.read(exact, old.capture_id).payload == b"existing"
        assert health.snapshot(scope)[0].capture_id is None
    finally:
        vault.close()


def test_revocation_after_link_and_rollback_failure(tmp_path, monkeypatch):
    import pytest
    import httpx
    from cifereca import http_trace
    from cifereca.core import capture_vault_service as module
    from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
    from cifereca.plugin_api.capture_vault import VaultError
    trace = http_trace.HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", trace)
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "instance", "account", "owner", "search")
    exact = CaptureScope("fixture", "instance", "account", "owner")
    with diagnostic_task_scope():
        ref = diagnostic_task_reference()
        exchange = trace.begin(httpx.Request("GET", "https://example.invalid/read"), "fixture")
        exchange.entry["outcome"] = "completed"
    health.observe(scope, Observation(Outcome.AUTH, "1", "now", ref), lease=health.lease())
    allowed = [True]
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: allowed[0], health=health)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        link = health.link_capture
        def revoke_after_link(*args, **kwargs):
            result = link(*args, **kwargs)
            assert result
            allowed[0] = False
            return result
        monkeypatch.setattr(health, "link_capture", revoke_after_link)
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.archive_search_trace(exact, ref, consented=True)
        allowed[0] = True
        assert port.sessions(exact) == ()
        assert health.snapshot(scope)[0].capture_id is None
        discard = vault._discard_failed_archive
        def fail_cleanup(*args):
            raise VaultError("vault_unsafe_file")
        monkeypatch.setattr(vault, "_discard_failed_archive", fail_cleanup)
        with pytest.raises(VaultError, match="vault_archive_rollback_failed"):
            port.archive_search_trace(exact, ref, consented=True)
        assert health.snapshot(scope)[0].capture_id is None
        allowed[0] = True
        assert port.sessions(exact) == ()
        ids = vault._storage.list_ids()
        assert len(ids) == 1
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.read(exact, ids[0])
        vault.maintain()
        assert vault._storage.list_ids() == ids
        monkeypatch.setattr(vault, "_discard_failed_archive", discard)
        vault.maintain()
        assert vault._storage.list_ids() == ()
        assert port.sessions(exact) == ()
    finally:
        vault.close()
