"""#4496: isolated setting command and audit, no library mutation."""
import json
from types import SimpleNamespace

import pytest

from cifereca.config import Config
from cifereca.core import destructive_cleanup_settings as settings
from cifereca.core.destructive_cleanup_policy import (
    DESTRUCTIVE_CLEANUP_KEY as KEY, destructive_cleanup_enabled,
)
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.storage import Storage


@pytest.fixture
def rig(tmp_path, monkeypatch):
    cfg = Config(tmp_path / "config.json")
    cfg.save()
    db = Storage(tmp_path / "library.sqlite")
    lock = tmp_path / "lock"
    monkeypatch.setattr(settings, "acquisition_lock_file", lambda: lock)
    try:
        yield SimpleNamespace(config=cfg, storage=db), lock
    finally:
        db.close()


def _change(service, enabled, *, expected=False, confirmed=True):
    return settings.change_destructive_cleanup_setting(
        service, expected_config=service.config, expected_enabled=expected,
        enabled=enabled, confirmed=confirmed,
    )


def _events(service):
    with service.storage._lock, service.storage._connect() as sql:
        return [(kind, outcome, json.loads(facts)) for kind, outcome, facts in sql.execute(
            "SELECT action_type,outcome,facts_json FROM action_events ORDER BY rowid"
        )]


def test_enable_and_disable_are_explicit_persisted_and_journaled(rig, tmp_path):
    service, _ = rig
    changed = _change(service, True)
    assert changed.enabled and changed.changed and changed.audit_complete
    assert destructive_cleanup_enabled(Config(service.config.path))
    no_change = _change(service, True, expected=True)
    assert not no_change.changed
    disabled = _change(service, False, expected=True)
    assert not disabled.enabled and disabled.changed and disabled.audit_complete
    assert not destructive_cleanup_enabled(Config(service.config.path))
    events = _events(service)
    assert [x[1] for x in events] == ["requested", "applied", "requested", "applied"]
    assert events[0][2] == events[1][2]
    assert events[2][2] == events[3][2]
    assert events[0][2]["request_id"] != events[2][2]["request_id"]
    assert str(tmp_path) not in repr(events)
    assert all(set(x[2]) == {"request_id", "previous_enabled", "requested_enabled"} for x in events)


@pytest.mark.parametrize("desired,confirmed", [(True, False), ("true", True), (1, True)])
def test_strict_confirmation_and_type(rig, desired, confirmed):
    service, _ = rig
    with pytest.raises(ValueError, match="confirmation_required"):
        _change(service, desired, confirmed=confirmed)
    assert not destructive_cleanup_enabled(Config(service.config.path))
    assert _events(service) == []


def test_stale_and_busy_cannot_enable(rig):
    service, lock = rig
    with pytest.raises(ValueError, match="stale"):
        _change(service, True, expected=True)
    with InterProcessLock(lock):
        with pytest.raises(LockBusyError):
            _change(service, True)
    assert _events(service) == []
    assert not destructive_cleanup_enabled(Config(service.config.path))


def test_activation_needs_audit_but_revocation_can_fail_safe(rig, monkeypatch):
    service, _ = rig
    def fail(**kwargs):
        raise OSError("private")
    with monkeypatch.context() as patch:
        patch.setattr(service.storage, "action_event_put", fail)
        with pytest.raises(ValueError, match="audit_unavailable"):
            _change(service, True)
    assert not destructive_cleanup_enabled(Config(service.config.path))
    assert _change(service, True).enabled
    monkeypatch.setattr(service.storage, "action_event_put", fail)
    result = _change(service, False, expected=True)
    assert result.changed and not result.enabled and not result.audit_complete
    assert not destructive_cleanup_enabled(Config(service.config.path))


def test_failed_completion_is_not_reported_as_complete(rig, monkeypatch):
    service, _ = rig
    original = service.storage.action_event_put
    def fail_applied(**kwargs):
        if kwargs["outcome"] == "applied":
            raise OSError("private")
        return original(**kwargs)
    monkeypatch.setattr(service.storage, "action_event_put", fail_applied)
    result = _change(service, True)
    assert result.changed and result.enabled and not result.audit_complete
    assert destructive_cleanup_enabled(Config(service.config.path))
    assert [x[1] for x in _events(service)] == ["requested"]


def test_failed_save_leaves_previous_value_and_failed_intent(rig, monkeypatch):
    service, _ = rig
    def fail():
        raise OSError("private")
    monkeypatch.setattr(service.config, "save", fail)
    with pytest.raises(ValueError, match="save_failed"):
        _change(service, True)
    assert not destructive_cleanup_enabled(service.config)
    assert not destructive_cleanup_enabled(Config(service.config.path))
    assert [x[1] for x in _events(service)] == ["requested", "save_failed"]


def test_nested_transaction_is_not_committed(rig):
    service, _ = rig
    sql = service.storage._connect()
    sql.execute("BEGIN IMMEDIATE")
    try:
        sql.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("test.cleanup.setting", "private"))
        with pytest.raises(ValueError, match="nested_transaction"):
            _change(service, True)
        assert sql.in_transaction
    finally:
        sql.rollback()
    assert service.storage.metadata_get("test.cleanup.setting") is None
    assert _events(service) == []


def test_general_settings_cannot_reactivate_or_revoke_stale_setting(rig):
    service, _ = rig
    for live, stale in ((False, True), (True, False)):
        service.config.set(KEY, live)
        data = {"maintenance": {"destructive_cleanup_enabled": stale, "future": 7},
                "unrelated": "retained"}
        result = settings.preserve_destructive_cleanup_setting(data, current=service.config)
        assert result["maintenance"] == {"destructive_cleanup_enabled": live, "future": 7}
        assert result["unrelated"] == "retained"


def test_setting_command_rotates_review_epoch_on_each_transition(rig):
    from cifereca.core.destructive_cleanup_policy import cleanup_review_epoch
    service, _ = rig
    initial = cleanup_review_epoch(service.config)
    _change(service, True)
    enabled = cleanup_review_epoch(service.config)
    _change(service, False, expected=True)
    disabled = cleanup_review_epoch(service.config)
    _change(service, True)
    renewed = cleanup_review_epoch(service.config)
    assert None not in (initial, enabled, disabled, renewed)
    assert len({initial, enabled, disabled, renewed}) == 4
    assert cleanup_review_epoch(Config(service.config.path)) != renewed
