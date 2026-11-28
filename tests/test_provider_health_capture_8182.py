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
        assert vault._storage.pending_ids() == ()
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
        # Simulate restart without the in-memory quarantine map.
        vault.close()
        reopened = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                                    authorize_scope=lambda producer, scope: True, health=health)
        try:
            reopened_port = reopened.for_plugin("ui.page.diagnostics")
            assert reopened._storage.pending_ids() == ids
            assert reopened_port.sessions(exact) == ()
            with pytest.raises(VaultError, match="vault_scope_denied"):
                reopened_port.read(exact, ids[0])
            reopened.maintain()
            assert reopened._storage.list_ids() == ()
            assert reopened._storage.pending_ids() == ()
            assert reopened_port.sessions(exact) == ()
        finally:
            reopened.close()
    finally:
        vault.close()


def test_pending_slots_and_bytes_count_once(tmp_path, monkeypatch):
    import os
    import pytest
    from cifereca.core import capture_vault as engine
    from cifereca.core import capture_vault_service as module
    from cifereca.plugin_api.capture_vault import VaultError
    monkeypatch.setattr(engine, "MAX_SESSIONS", 2)
    monkeypatch.setattr(module, "MAX_SESSIONS", 2)
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True)
    handles = []
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        exact = CaptureScope("fixture", "instance", "account", "owner")
        first = port.begin(exact, "first", consented=True)
        second = port.begin(exact, "second", consented=True)
        storage = vault._storage
        key = vault._key()
        for writer in (first, second):
            handles.append(storage.open_pending(writer.capture_id, key, create=True))
        fd = storage._open_dir(create=False)
        try:
            assert storage._entries(fd) == (2, 64)
        finally:
            os.close(fd)
        with pytest.raises(VaultError, match="vault_limit"):
            storage.open_pending("f" * 32, key, create=True)
        first.save(b"fixture", "completed")
        fd = storage._open_dir(create=False)
        try:
            count, total = storage._entries(fd)
            assert count == 2
            assert total == 64 + (storage.path / (first.capture_id + ".cbrv")).stat().st_size
        finally:
            os.close(fd)
        assert port.sessions(exact) == ()
        storage.clear_pending(first.capture_id, handles[0])
        assert port.read(exact, first.capture_id).payload == b"fixture"
    finally:
        for handle in handles:
            os.close(handle)
        vault.close()


def test_live_pending_capture_not_pruned_by_other_core(tmp_path):
    import os
    import pytest
    from datetime import datetime, timezone, timedelta
    from cifereca.plugin_api.capture_vault import VaultError
    def open_vault():
        return CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                                authorize_scope=lambda producer, scope: True)
    vault = open_vault()
    other = open_vault()
    handle = None
    try:
        exact = CaptureScope("fixture", "instance", "account", "owner")
        writer = vault.for_plugin("ui.page.diagnostics").begin(exact, "live", consented=True)
        handle = vault._storage.open_pending(writer.capture_id, vault._key(), create=True)
        writer.save(b"fixture", "completed")
        assert other.maintain(now=datetime.now(timezone.utc) + timedelta(days=366)) == (0, 0)
        assert other._storage.list_ids() == (writer.capture_id,)
        port = other.for_plugin("ui.page.diagnostics")
        assert port.sessions(exact) == ()
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.read(exact, writer.capture_id)
        vault._storage.clear_pending(writer.capture_id, handle)
        assert port.read(exact, writer.capture_id).payload == b"fixture"
    finally:
        if handle is not None:
            os.close(handle)
        vault.close()
        other.close()

def test_multicapability_archive_and_exact_export(tmp_path, monkeypatch):
    import pytest
    import httpx
    from cifereca import http_trace
    from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
    from cifereca.plugin_api.capture_vault import VaultError

    trace = http_trace.HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", trace)
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    health = PassiveHealth()
    health.set_enabled(True)
    exact = CaptureScope("fixture", "instance", "account", "owner")
    other = CaptureScope("fixture", "instance", "other", "owner")
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True, health=health)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        for capability in ("detail", "rights", "loans", "personal_library"):
            scope = Scope("fixture", "instance", "account", "owner", capability)
            with diagnostic_task_scope():
                ref = diagnostic_task_reference()
                exchange = trace.begin(httpx.Request("GET", "https://example.invalid/read"), "fixture")
                exchange.entry["outcome"] = "completed"
            health.observe(scope, Observation(Outcome.AUTH, "1", "now", ref), lease=health.lease())
            with pytest.raises(VaultError, match="vault_confirmation_required"):
                port.archive_provider_trace(exact, capability, ref)
            for wrong in ("search", "unsupported"):
                with pytest.raises(VaultError, match="vault_scope_denied"):
                    port.archive_provider_trace(exact, wrong, ref, consented=True)
            with pytest.raises(VaultError, match="vault_scope_denied"):
                port.archive_provider_trace(other, capability, ref, consented=True)
            cid = port.archive_provider_trace(exact, capability, ref, consented=True)
            assert health.snapshot(scope)[0].capture_id == cid
            destination = tmp_path / (capability + ".zip")
            with pytest.raises(VaultError, match="vault_confirmation_required"):
                port.export_http_archive(cid, str(destination), scope=exact)
            for wrong_scope in (CaptureScope(), other):
                with pytest.raises(VaultError, match="vault_scope_denied"):
                    port.export_http_archive(cid, str(destination), scope=wrong_scope, confirmed=True)
            assert not destination.exists()
            port.export_http_archive(cid, str(destination), scope=exact, confirmed=True)
            assert destination.stat().st_mode & 0o777 == 0o600
    finally:
        vault.close()


def test_guarded_archive_and_export_revocation(tmp_path, monkeypatch):
    import pytest
    import httpx
    from cifereca import http_trace
    from cifereca.core import capture_export, capture_vault_service
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
    generation = (0, 0, "fixture", 1)
    revoked = [False]
    def selection(s, cap, evidence, cid, token):
        return (not revoked[0] and s == exact and cap == "search" and evidence == ref
                and token == generation and health.snapshot(scope)[0].capture_id == cid)
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda *_: True, health=health,
                             selection_current=selection)
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.archive_provider_trace(exact, "search", ref, consented=True)
        args = dict(consented=True, generation=generation)
        original = capture_vault_service._Writer.save
        def save_then_revoke(writer, *a, **kw):
            original(writer, *a, **kw)
            revoked[0] = True
        with monkeypatch.context() as patch:
            patch.setattr(capture_vault_service._Writer, "save", save_then_revoke)
            with pytest.raises(VaultError, match="vault_scope_denied"):
                port.archive_provider_trace(exact, "search", ref, **args)
        assert vault._storage.pending_ids() == ()
        assert port.sessions(exact) == ()
        assert health.snapshot(scope)[0].capture_id is None
        revoked[0] = False
        original_link = health.link_capture
        def link_then_revoke(*a, **kw):
            result = original_link(*a, **kw)
            revoked[0] = True
            return result
        with monkeypatch.context() as patch:
            patch.setattr(health, "link_capture", link_then_revoke)
            with pytest.raises(VaultError, match="vault_scope_denied"):
                port.archive_provider_trace(exact, "search", ref, **args)
        assert vault._storage.pending_ids() == ()
        assert port.sessions(exact) == ()
        assert health.snapshot(scope)[0].capture_id is None
        revoked[0] = False
        cid = port.archive_provider_trace(exact, "search", ref, **args)
        destination = tmp_path / "private.zip"
        export_args = dict(confirmed=True, scope=exact, capability="search",
                           evidence_ref=ref, generation=generation)
        with pytest.raises(VaultError, match="vault_scope_denied"):
            port.export_http_archive(cid, str(destination), confirmed=True, scope=exact)
        original_fsync = capture_export.os.fsync
        def fsync_then_revoke(fd):
            original_fsync(fd)
            revoked[0] = True
        with monkeypatch.context() as patch:
            patch.setattr(capture_export.os, "fsync", fsync_then_revoke)
            with pytest.raises(VaultError, match="vault_scope_denied"):
                port.export_http_archive(cid, str(destination), **export_args)
        assert not destination.exists()
        assert not list(tmp_path.glob(".cifereca-export-*"))
        revoked[0] = False
        port.export_http_archive(cid, str(destination), **export_args)
        assert destination.stat().st_mode & 0o777 == 0o600
    finally:
        vault.close()


def test_health_archive_restores_history_not_latest(tmp_path, monkeypatch):
    import httpx
    from cifereca import http_trace
    from cifereca.diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference
    trace = http_trace.HTTPTraceCapture()
    monkeypatch.setattr(http_trace, "HTTP_TRACE", trace)
    monkeypatch.setattr(http_trace, "_trace_active", lambda: True)
    exact = CaptureScope("fixture", "i", "a", "owner")
    scope = Scope("fixture", "i", "a", "owner", "search")
    health = PassiveHealth()
    health.set_enabled(True)
    with diagnostic_task_scope():
        ref = diagnostic_task_reference()
        exchange = trace.begin(httpx.Request("GET", "https://example.invalid/read"), "fixture")
        exchange.entry["outcome"] = "completed"
    health.observe(scope, Observation(Outcome.SUCCESS, "1", "now", ref), lease=health.lease())
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True, health=health)
    try:
        capture = vault.for_plugin("ui.page.diagnostics").archive_search_trace(exact, ref, consented=True)
    finally:
        vault.close()
    fresh = PassiveHealth()
    fresh.set_enabled(True)
    vault = CoreCaptureVault(tmp_path / "vault", tmp_path / "keys",
                             authorize_scope=lambda producer, scope: True, health=fresh,
                             restore_current=lambda scope, generation: generation == (1,))
    try:
        port = vault.for_plugin("ui.page.diagnostics")
        assert port.restore_provider_health_history(exact, (1,), confirmed=True) == 1
        latest, baseline, anomalies = fresh.snapshot(scope)
        assert latest is None and baseline.capture_id == capture and not anomalies
        assert fresh.drain_transitions() == ()
        assert port.restore_provider_health_history(exact, (1,), confirmed=True) == 0
    finally:
        vault.close()
