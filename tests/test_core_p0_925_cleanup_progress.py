"""#925: garbage collection is a real nested update phase, including failure."""
from __future__ import annotations

import io

import pytest

from cifereca import updater
from cifereca.update_progress import update_operation
from tests.test_updater import _transactional_update_fixture, _release_with_plugin_manifest


@pytest.mark.parametrize("fails", [False, True])
def test_cleanup_phase_preserves_call_and_transaction_rollback(tmp_path, monkeypatch, fails):
    root, _old, packages, baseline = _transactional_update_fixture(tmp_path, monkeypatch)
    previous = updater._symlink_target(root / "current").resolve()
    identity = updater._package_identity("org.test")
    source = _release_with_plugin_manifest(tmp_path / "next-source", "3.0.0")
    original_gc, calls = updater._gc, []
    def collect(actual_root, keep):
        calls.append((actual_root, keep))
        if fails:
            raise RuntimeError("PRIVATE-TOKEN /home/private/runtime")
        return original_gc(actual_root, keep)
    monkeypatch.setattr(updater, "_gc", collect)
    events, stream = [], io.StringIO()
    if fails:
        with pytest.raises(RuntimeError):
            with update_operation("source", callback=events.append, stream=stream):
                updater.install_source(source, keep=4)
        assert updater._symlink_target(root / "current").resolve() == previous
        assert updater._package_identity("org.test") == identity
        assert [e["status"] for e in events if e["stage"] == "rollback"] == ["ROLLBACK", "OK"]
    else:
        with update_operation("source", callback=events.append, stream=stream):
            result = updater.install_source(source, keep=4)
        assert result["ok"] is True
    assert calls == [(root, 4)]
    cleanup = [e for e in events if e["stage"] == "cleanup"]
    assert [e["status"] for e in cleanup] == ["RUNNING", "ERROR" if fails else "OK"]
    if fails:
        assert cleanup[-1]["reason_code"] == "internal_error"
    assert events[-1]["outcome"] == ("failed" if fails else "success")
    assert "PRIVATE" not in stream.getvalue() + repr(events)
    assert "percent" not in repr(events)
