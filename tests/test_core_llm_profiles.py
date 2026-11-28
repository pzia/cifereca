"""Offline profile and restart-admission contract tests (#8467/#8469)."""
import json

import pytest

from cifereca.config import Config
from cifereca.core.llm_profiles import (
    LLMSettingsError, normalize_profiles, selected_settings, select_profile,
    store_profile, delete_profile, enable_profile,
)
from cifereca.core.llm_service import optional_llm_service
from cifereca.plugin_api import LLMRequest

LOCAL = {"endpoint": "http://127.0.0.1:11434/v1/chat/completions", "model": "one"}
REMOTE = {"endpoint": "https://example.invalid/v1/chat/completions", "model": "two",
          "api_key_env": "TEST_KEY"}


def test_empty_is_off():
    cfg = normalize_profiles({})
    assert cfg["profiles"] == {}
    assert cfg["active_profile"] is None
    assert optional_llm_service({"llm": cfg}) is None


def test_local_migration_idempotent_and_round_trip(tmp_path):
    cfg = normalize_profiles({**LOCAL, "enabled": True, "api_key_env": "TEST_KEY"})
    assert normalize_profiles(cfg) == cfg
    assert selected_settings(cfg)["model"] == "one"
    assert selected_settings(cfg)["api_key_env"] == "TEST_KEY"
    path = tmp_path / "config.json"
    c = Config(path)
    c.set("llm", cfg)
    c.save()
    assert Config(path).get("llm") == cfg
    assert optional_llm_service(Config(path)).available()


def test_remote_legacy_requires_new_explicit_approval():
    cfg = normalize_profiles({**REMOTE, "enabled": True, "allow_remote": True})
    assert not cfg["enabled"]
    assert cfg["remote_approval"] is None
    assert selected_settings(cfg)["endpoint"] == REMOTE["endpoint"]
    with pytest.raises(LLMSettingsError):
        enable_profile(cfg, enabled=True)
    approved = enable_profile(cfg, enabled=True, remote_confirmed=True)
    assert selected_settings(approved)["allow_remote"] is True
    assert optional_llm_service({"llm": approved}).destination()["remote"] is True


def test_manual_selection_and_removal_no_fallback():
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    cfg = store_profile(cfg, "remote", REMOTE)
    assert selected_settings(cfg)["model"] == "one"
    switched = select_profile(cfg, "remote")
    assert not switched["enabled"]
    approved = enable_profile(switched, enabled=True, remote_confirmed=True)
    assert selected_settings(approved)["model"] == "two"
    switched_back = select_profile(approved, "legacy")
    assert not switched_back["enabled"] and switched_back["remote_approval"] is None
    removed = delete_profile(approved, "remote")
    assert removed["active_profile"] is None and not removed["enabled"]
    assert "legacy" in removed["profiles"]


@pytest.mark.parametrize("change", [
    {"endpoint": "https://example.invalid:8443/v1/chat/completions"},
    {"endpoint": "https://example.invalid/v2/chat/completions"},
    {"model": "other"}, {"api_key_env": "OTHER_KEY"},
])
def test_destination_change_revokes_permission(change):
    cfg = normalize_profiles({**REMOTE, "enabled": True, "allow_remote": True})
    cfg = enable_profile(cfg, enabled=True, remote_confirmed=True)
    changed = store_profile(cfg, "legacy", {**REMOTE, **change})
    assert not changed["enabled"] and changed["remote_approval"] is None
    tampered = {**cfg, "profiles": {"legacy": {**REMOTE, **change}}}
    assert optional_llm_service({"llm": tampered}) is None


def test_old_runtime_cannot_send_after_saved_switch(monkeypatch):
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    cfg = store_profile(cfg, "remote", REMOTE)
    root = {"llm": cfg}
    service = optional_llm_service(root)
    assert service.available()
    def forbidden(*args, **kwargs):
        pytest.fail("stale service performed HTTP")
    monkeypatch.setattr(service, "_opener", forbidden)
    root["llm"] = select_profile(cfg, "remote")
    assert not service.available()
    assert service.generate(LLMRequest("private")).reason == "unavailable"
    assert optional_llm_service(root) is None


@pytest.mark.parametrize("patch", [
    {"schema_version": True}, {"schema_version": 2}, {"enabled": "true"},
    {"profiles": []}, {"active_profile": "missing"}, {"active_profile": []},
    {"profiles": {"legacy": {**LOCAL, "api_key": "SECRET"}}},
    {"profiles": {"legacy": {**LOCAL, "allow_remote": True}}},
])
def test_malformed_profiles_never_fall_back(patch):
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    malformed = {**cfg, **patch}
    with pytest.raises(LLMSettingsError) as error:
        normalize_profiles(malformed)
    assert "SECRET" not in str(error.value)
    assert optional_llm_service({"llm": malformed}) is None


def test_malformed_persisted_profile_marker_is_not_merged_with_legacy(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm": {"schema_version": 2, **LOCAL, "enabled": True}}))
    cfg = Config(path)
    assert optional_llm_service(cfg) is None
    assert cfg.get("llm.schema_version") == 2


def test_profile_bounds_and_unknown_secret_fields():
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    with pytest.raises(LLMSettingsError):
        store_profile(cfg, "legacy", {**LOCAL, "api_key": "SECRET"})
    with pytest.raises(LLMSettingsError):
        normalize_profiles({**cfg, "profiles": {f"p{i}": LOCAL for i in range(33)},
                            "active_profile": None, "enabled": False})


def test_saved_change_then_restore_never_reactivates_old_instance(tmp_path):
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    c = Config(tmp_path / "config.json")
    c.set("llm", cfg)
    service = optional_llm_service(c)
    assert service.available()
    c.set("llm", select_profile(cfg, None))
    c.set("llm", cfg)
    assert not service.available()
    assert optional_llm_service(c).available()


def test_unrelated_settings_do_not_revoke_llm(tmp_path):
    c = Config(tmp_path / "config.json")
    c.set("llm", normalize_profiles({**LOCAL, "enabled": True}))
    service = optional_llm_service(c)
    c.set("ui.width", 1400)
    c.save()
    assert service.available()


def test_metrics_have_bounded_labels_and_no_payload(monkeypatch):
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    service = optional_llm_service({"llm": cfg})
    def refuse(*args, **kwargs):
        raise OSError("SECRET_SENTINEL")
    monkeypatch.setattr(service, "_opener", refuse)
    assert service.generate(LLMRequest("PRIVATE_PROMPT")).reason == "backend_unavailable"
    metrics = service.metrics_snapshot()
    assert set(metrics) == {"local:backend_unavailable"}
    assert metrics["local:backend_unavailable"]["count"] == 1
    assert "PRIVATE_PROMPT" not in repr(metrics)
    assert "SECRET_SENTINEL" not in repr(metrics)
    metrics["local:backend_unavailable"]["count"] = 99
    assert service.metrics_snapshot()["local:backend_unavailable"]["count"] == 1


def test_revocation_drops_inflight_result_and_preserves_no_fallback(monkeypatch):
    from io import BytesIO
    from threading import Event, Thread

    cfg = normalize_profiles({**LOCAL, "enabled": True})
    root = {"llm": cfg}
    service = optional_llm_service(root)
    entered, release, exited = Event(), Event(), Event()
    answers = []

    def delayed(*_args, **_kwargs):
        entered.set()
        try:
            assert release.wait(5)
            return BytesIO(b'{"choices":[{"message":{"content":"PRIVATE_RESULT"}}]}')
        finally:
            exited.set()

    monkeypatch.setattr(service, "_opener", delayed)
    caller = Thread(target=lambda: answers.append(service.generate(LLMRequest("private"))))
    caller.start()
    try:
        assert entered.wait(2)
        root["llm"] = select_profile(cfg, None)
        caller.join(2)
        assert not caller.is_alive()
        assert len(answers) == 1 and answers[0].reason == "unavailable"
        assert not answers[0].text
        root["llm"] = cfg
        assert not service.available()
    finally:
        release.set()
        caller.join(5)
        assert exited.wait(5)


def test_two_slots_saturate_without_retry_or_fallback(monkeypatch):
    from io import BytesIO
    from threading import Event, Lock, Thread

    cfg = normalize_profiles({**LOCAL, "enabled": True})
    service = optional_llm_service({"llm": cfg})
    entered, release = Event(), Event()
    lock = Lock()
    calls, results = [], []

    def blocked(request, **_kwargs):
        with lock:
            calls.append(request.full_url)
            if len(calls) == 2:
                entered.set()
        assert release.wait(5)
        return BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')

    monkeypatch.setattr(service, "_opener", blocked)
    threads = [Thread(target=lambda: results.append(service.generate(LLMRequest("fixture"))))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        assert entered.wait(2)
        assert service.generate(LLMRequest("third")).reason == "busy"
        assert calls == [LOCAL["endpoint"]] * 2
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
    assert len(results) == 2 and all(r.ok for r in results)
    assert service.metrics_snapshot()["local:busy"]["count"] == 1


@pytest.mark.parametrize("hidden", ["\u061c", "\u200e", "\u200f"])
def test_profile_disclosure_rejects_hidden_controls(hidden):
    cfg = normalize_profiles({**LOCAL, "enabled": True})
    with pytest.raises(LLMSettingsError):
        store_profile(cfg, "other", {**LOCAL, "model": "model" + hidden})
