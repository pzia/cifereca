"""P0 #4858: retired W2 has no executable provider/legacy mutation path."""
from __future__ import annotations

import pytest

from cifereca.core.kobo_backup import _sync_kobo_backup_locked
from cifereca.providers import ProviderConfigurationError
from tests.test_source_materialization_confirmation import case  # noqa: F401


class _NoLegacyAccess:
    """Any attempt to resolve historical sessions/storage or execute GET is fatal."""

    def __getattr__(self, name):
        pytest.fail(f"retired W2 accessed {name}")


@pytest.mark.parametrize("credential_id,confirmed_manifest", [
    (None, None),
    ("vault-1", None),
    ("vault-1", []),
    ("vault-1", [{"product_id": "book-1", "downloadable": True}]),
])
def test_w2_refuses_before_legacy_read_write_or_get(credential_id, confirmed_manifest):
    with pytest.raises(ProviderConfigurationError, match="kobo_legacy_w2_retired_use_canonical_provider"):
        _sync_kobo_backup_locked(
            _NoLegacyAccess(), logger=_NoLegacyAccess(),
            credential_id=credential_id, confirmed_manifest=confirmed_manifest,
            progress=lambda *_args, **_kwargs: pytest.fail("unexpected progress"),
            source_guard=lambda: pytest.fail("unexpected source guard"),
            item_guard=lambda *_args: pytest.fail("unexpected item guard"),
        )


def test_w2_refuses_even_when_cancellation_requested():
    with pytest.raises(ProviderConfigurationError, match="kobo_legacy_w2_retired_use_canonical_provider"):
        _sync_kobo_backup_locked(
            _NoLegacyAccess(), logger=_NoLegacyAccess(),
            cancelled=lambda: pytest.fail("unexpected cancellation callback"),
        )


def test_public_sync_w2_refuses_before_lock_creation():
    from cifereca.core.kobo_backup import sync_kobo_backup

    with pytest.raises(ProviderConfigurationError, match="kobo_legacy_w2_retired_use_canonical_provider"):
        sync_kobo_backup(
            _NoLegacyAccess(),
            credential_id="vault-1", retry_scope_blocked=True,
            lock_factory=lambda *_args: pytest.fail("legacy lock must not be created"),
            lock_path=lambda: pytest.fail("legacy lock path must not be resolved"),
            cancelled=lambda: pytest.fail("legacy cancellation callback must not run"),
        )


def test_d2_partial_abi_cannot_prepare_legacy_confirmation(case, monkeypatch):
    from cifereca.core import source_materialization

    monkeypatch.setattr(
        case.target, "backup_manifest",
        lambda: pytest.fail("D2 must not read a legacy manifest"),
    )
    prepared = source_materialization.prepare(case.broker, case.source)
    assert not prepared.ok
    assert prepared.facts["status"] == "canonical_delivery_unavailable"
    assert "confirmation_token" not in prepared.facts
    assert not case.downloaded and not any(case.calls.values())
    replay = source_materialization.execute(
        case.broker, case.source, "a" * 64,
    )
    assert not replay.ok
    assert replay.facts["status"] == "canonical_delivery_unavailable"
    assert not case.downloaded and not any(case.calls.values())


def test_d3_legacy_direct_executor_is_removed():
    from pathlib import Path
    from cifereca.core.kobo_backup import _execute_kobo_acquisition

    with pytest.raises(ProviderConfigurationError, match="kobo_legacy_direct_get_retired"):
        _execute_kobo_acquisition(_NoLegacyAccess(), _NoLegacyAccess(), Path("."))


def test_d4_retired_on_start_never_persists_attempt():
    from cifereca.core import source_materialization_schedule as schedule

    assert not schedule.can_enable_kobo_on_start(_NoLegacyAccess(), _NoLegacyAccess())
    assert schedule.kobo_on_start_sessions(_NoLegacyAccess()) == ()
    assert not schedule.on_start_due(_NoLegacyAccess(), _NoLegacyAccess())
    assert not schedule.mark_on_start_attempt(_NoLegacyAccess(), _NoLegacyAccess())


def test_d1_partial_abi_cannot_refresh_legacy_manifest(tmp_path, monkeypatch):
    from tests.test_kobo_backup_guard import _service
    from cifereca.core.kobo_backup import kobo_backup_preview

    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: pytest.fail("D1 must not read a legacy manifest"),
    )
    try:
        with pytest.raises(ProviderConfigurationError, match="canonical_kobo_preview_abi_unavailable"):
            kobo_backup_preview(service, "vault-1", refresh_manifest=True)
        assert service.storage.backup_list("kobo") == []
    finally:
        service.close()


@pytest.mark.parametrize("account_index", [0, 1, 2])
@pytest.mark.parametrize("legacy_token", ["", "a" * 64, "not-a-token"])
def test_all_bound_profiles_refuse_legacy_w2_confirmation_and_get(
    case, monkeypatch, account_index, legacy_token,
):
    """Old W2 tokens are not transferable or executable for any borrower.

    Three independent connected sessions, with distinct credentials and
    identities, must all fail closed. No provider manifest, acquisition,
    backup writer or checkpoint is contacted by a mere confirmation attempt.
    """
    from cifereca.core import source_materialization as materialize

    session = case.sessions[account_index]
    source_id = case.broker._source_id(session.accounts[0])
    monkeypatch.setattr(
        session, "backup_manifest",
        lambda: pytest.fail("retired W2 must not GET provider inventory"),
    )
    monkeypatch.setattr(
        case.service.storage, "backup_put",
        lambda *_a, **_k: pytest.fail("retired W2 must not write backup_items"),
    )
    before_calls = dict(case.calls)
    preview = materialize.prepare(case.broker, source_id)
    assert not preview.ok
    assert preview.facts["status"] == "canonical_delivery_unavailable"
    assert "confirmation_token" not in preview.facts
    replay = materialize.execute(case.broker, source_id, legacy_token)
    assert not replay.ok
    assert replay.facts["status"] == "canonical_delivery_unavailable"
    assert case.calls == before_calls
    assert not case.downloaded
