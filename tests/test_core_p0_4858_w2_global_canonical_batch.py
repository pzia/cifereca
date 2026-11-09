"""#4858 W2: account-wide Kobo sync cannot call one mixed legacy writer.

The real LibraryService facade and connected provider session registry are
used; scoped sync executors are spies because the canonical A->B worker and
0.7 compatibility are independently tested at their own admission boundary.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core import kobo_backup
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service


def _modern(session, monkeypatch):
    for name in (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate_runtime_guarded",
    ):
        monkeypatch.setattr(
            session, name, lambda *_a, **_kw:
            pytest.fail("global selection may not invoke provider ABI"),
            raising=False,
        )


def _legacy(credential):
    return SimpleNamespace(
        provider_id="kobo", credential_id=credential, connected=True,
        capabilities=SimpleNamespace(backup=True),
    )


def _fake_report(status="complete", *, modern=True, downloaded=0, unchanged=0):
    return {
        "scope": "account", "status": status, "downloaded": downloaded,
        "unchanged": unchanged, "completed": downloaded + unchanged,
        "execution_path": "canonical" if modern else "legacy",
        "legacy_backup_mutations": 0 if modern else 1,
    }


@pytest.fixture
def rig(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    _modern(session, monkeypatch)
    monkeypatch.setattr(
        kobo_backup, "sync_kobo_backup", lambda *_a, **_kw:
        pytest.fail("global 0.7 worker must never own modern Kobo session"),
    )
    try:
        yield service, session
    finally:
        service.close()


def test_global_one_modern_delegates_to_exact_scoped_canonical(rig, monkeypatch):
    service, session = rig
    calls = []
    monkeypatch.setattr(
        service, "sync_kobo_backup_account",
        lambda credential: (
            calls.append(credential)
            or _fake_report(downloaded=2, unchanged=3)
        ),
    )
    events = []
    report = service.sync_kobo_backup(events.append)
    assert calls == ["vault-1"]
    assert report["status"] == "complete"
    assert report["scope"] == "all" and report["sessions"] == 1
    assert report["canonical_sources"] == 1
    assert report["compatibility_sources"] == 0
    assert report["legacy_batch_writer"] is False
    assert report["downloaded"] == 2 and report["unchanged"] == 3
    assert [x["event"] for x in events] == ["source_done", "done"]
    assert "vault-1" not in str(report)
    assert "vault-1" not in str(events)
    assert service.storage.backup_list() == []


def test_mixed_modern_and_legacy_are_scoped_independently(rig, monkeypatch):
    service, session = rig
    second = _legacy("vault-2")
    service._sessions[service._session_key("kobo", "vault-2")] = second
    calls = []
    def scoped(credential):
        calls.append(credential)
        return (
            _fake_report(downloaded=1)
            if credential == "vault-1"
            else _fake_report(modern=False, unchanged=2)
        )
    monkeypatch.setattr(service, "sync_kobo_backup_account", scoped)
    report = service.sync_kobo_backup()
    assert calls == ["vault-1"]  # Partial ABI never reaches account dispatch.
    assert report["status"] == "partial"
    assert report["canonical_sources"] == 1
    assert report["compatibility_sources"] == 1
    assert report["downloaded"] == 1 and report["unchanged"] == 0
    assert report["errors"] == 1
    assert all("credential_id" not in row for row in report["sources"])


def test_one_scoped_failure_does_not_poison_other_credentials(rig, monkeypatch):
    service, session = rig
    service._sessions[service._session_key("kobo", "vault-2")] = _legacy("vault-2")
    calls = []
    def scoped(credential):
        calls.append(credential)
        if credential == "vault-1":
            raise RuntimeError("signed URL and private token must not escape")
        return _fake_report(modern=False, downloaded=1)
    monkeypatch.setattr(service, "sync_kobo_backup_account", scoped)
    report = service.sync_kobo_backup()
    assert calls == ["vault-1"]
    assert report["status"] == "failed"
    assert report["errors"] == 2
    assert report["downloaded"] == 0
    assert "signed URL" not in str(report)
    assert "private token" not in str(report)


def test_modern_session_change_between_sources_does_not_fall_back_to_w2(
    rig, monkeypatch,
):
    service, first = rig
    other = _legacy("vault-0")
    _modern(other, monkeypatch)
    service._sessions[service._session_key("kobo", "vault-0")] = other
    calls = []
    def scoped(credential):
        calls.append(credential)
        if credential == "vault-0":
            # Simulate a reconnect/rebind before the second source dispatch.
            service._sessions[service._session_key("kobo", "vault-1")] = _legacy("vault-1")
        return _fake_report(downloaded=1)
    monkeypatch.setattr(service, "sync_kobo_backup_account", scoped)
    report = service.sync_kobo_backup()
    assert calls == ["vault-0"]
    assert report["status"] == "partial"
    assert report["errors"] == 1
    assert report["downloaded"] == 1


def test_duplicate_credential_sessions_fail_before_any_global_get(
    rig, monkeypatch,
):
    service, session = rig
    other = _legacy("vault-1")
    service._sessions["untrusted-duplicate-session"] = other
    monkeypatch.setattr(
        service, "sync_kobo_backup_account",
        lambda *_a, **_kw: pytest.fail("should not sync ambiguous source"),
    )
    with pytest.raises(ProviderConfigurationError,
                       match="kobo_global_canonical_source_ambiguous"):
        service.sync_kobo_backup()


def test_all_legacy_fails_closed_before_get_or_write(tmp_path, monkeypatch):
    service = _service(tmp_path)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_k: pytest.fail("legacy GET forbidden"),
    )
    monkeypatch.setattr(
        service.storage, "backup_put",
        lambda *_a, **_k: pytest.fail("legacy W2 writer forbidden"),
    )
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_legacy_w2_retired_use_canonical_provider"):
            service.sync_kobo_backup()
    finally:
        service.close()
