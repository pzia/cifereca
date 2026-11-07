"""#4858 W2: explicit Kobo materialization delegates to the canonical A->B executor.

No legacy backup writes, rights-by-SHA, direct provider GET, or bypass of
persisted source consent. The Core source Sync has independent A/B E2E tests.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import kobo_backup, source_materialization as materialize
from cifereca.plugin_api import ProviderRightsScope, ProviderRightsSnapshot, ProviderRightObservation, SyncResult
from tests.test_source_materialization_confirmation import case, _prepare  # noqa: F401


def _snapshot(case, *, revision="r1"):
    account = case.target.accounts[0]
    return ProviderRightsSnapshot(
        scope=ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        ),
        rights=(ProviderRightObservation(
            edition_id="PRIVATE-book-1", right_id="purchase",
            kind="purchase", state="active",
            evidence_kind="provider_response", evidence_ref="synthetic",
            observed_at="2026-11-07T06:00:00+00:00",
            delivery="available", delivery_offer_id="offer-one",
            provider_revision_id=revision,
        ),),
        status="success", coverage="partial", branch="purchases",
    )


@pytest.fixture
def canonical(case, monkeypatch):
    methods = (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate",
    )
    for name in methods:
        monkeypatch.setattr(
            case.target, name,
            lambda *_a, **_k: pytest.fail("Core must own its real I/O"),
            raising=False,
        )
    from cifereca.core import source_sync_consent, rights_snapshot_read
    from cifereca.core import convergence_verified_presence

    policy = [source_sync_consent.Policy("on", "a" * 32)]
    snapshots = [_snapshot(case)]
    calls = []
    monkeypatch.setattr(
        source_sync_consent, "read_policy",
        lambda *_a, **_k: policy[0],
    )
    monkeypatch.setattr(
        rights_snapshot_read, "read_account_rights_snapshot",
        lambda *_a, **_k: snapshots[0],
    )
    monkeypatch.setattr(
        convergence_verified_presence, "plan_existing_verified_presence",
        lambda *_a, **_k: NS(
            decisions=(NS(provider_satisfied=False, state="pending"),),
        ),
    )

    def forbidden(*_a, **_k):
        pytest.fail("W2 legacy manifest, backup or direct GET must not run")

    monkeypatch.setattr(case.target, "backup_manifest", forbidden)
    monkeypatch.setattr(kobo_backup, "_sync_kobo_backup_locked", forbidden)
    monkeypatch.setattr(case.service.storage, "backup_list", forbidden)
    monkeypatch.setattr(case.service.storage, "backup_put", forbidden)

    counts = {
        name: (1 if name == "downloaded" else 0)
        for name in (
            "downloaded", "recovered", "present_attested",
            "pending", "blocked", "failed", "cancelled",
            "skipped_archived", "inactive",
        )
    }

    def core_sync(source_id, **kwargs):
        calls.append(source_id)
        assert source_id == case.source
        return SyncResult(ok=True, changed=1, facts={
            "canonical_execution": counts.copy(),
        })

    # The real broker is slotted: patch its class method, never its instance.
    # Failed instance patch registration can poison pytest's later undo stack.
    monkeypatch.setattr(
        type(case.broker), "synchronize_source",
        lambda self, source_id, **kwargs: core_sync(source_id, **kwargs),
    )
    return NS(case=case, policy=policy, snapshots=snapshots, calls=calls, counts=counts)


def test_modern_manual_preview_uses_provider_a_and_core_presence_not_backup(canonical):
    case = canonical.case
    plan = case.broker.prepare_local_materialization(case.source)
    assert plan.ok and plan.facts["status"] == "prepared"
    assert plan.facts["execution_path"] == "canonical"
    assert plan.facts["acquisition_authorized"] is False
    assert plan.facts["counts"]["potentially_missing"] == 1
    assert not canonical.calls
    assert not case.downloaded and not any(case.calls.values())


def test_confirm_dispatches_canonical_sync_once_with_no_legacy_get(canonical):
    case = canonical.case
    token = case.broker.prepare_local_materialization(
        case.source,
    ).facts["confirmation_token"]
    result = case.broker.materialize_source(case.source, token)
    assert result.ok and result.changed == 1
    assert result.facts["status"] == "success"
    assert result.facts["execution_path"] == "canonical"
    assert canonical.calls == [case.source]
    assert not case.downloaded and not any(case.calls.values())
    replay = case.broker.materialize_source(case.source, token)
    assert replay.ok is False
    assert replay.facts["status"] == "confirmation_unavailable"
    assert canonical.calls == [case.source]


@pytest.mark.parametrize("change", ("revision", "consent_off", "consent_renewed"))
def test_stale_provider_a_or_consent_cannot_dispatch(canonical, change):
    case = canonical.case
    token = case.broker.prepare_local_materialization(
        case.source,
    ).facts["confirmation_token"]
    from cifereca.core.source_sync_consent import Policy
    if change == "revision":
        canonical.snapshots[0] = _snapshot(case, revision="r2")
    elif change == "consent_off":
        canonical.policy[0] = Policy("off", "b" * 32)
    else:
        canonical.policy[0] = Policy("on", "b" * 32)
    result = case.broker.materialize_source(case.source, token)
    assert result.ok is False
    assert result.facts["status"] == (
        "preview_changed" if change == "revision" else "canonical_consent_changed"
    )
    assert canonical.calls == []
    assert not case.downloaded


def test_empty_or_inventory_only_core_sync_is_not_false_completed_delivery(canonical):
    case = canonical.case
    def no_executor(_source, **_kwargs):
        canonical.calls.append("metadata_only")
        return SyncResult(ok=True, facts={"status": "success"})
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setattr(
            type(case.broker), "synchronize_source",
            lambda self, source_id, **kwargs: no_executor(source_id, **kwargs),
        )
        token = case.broker.prepare_local_materialization(
            case.source,
        ).facts["confirmation_token"]
        result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.changed == 0
    assert result.facts["status"] == "canonical_execution_unavailable"
    assert canonical.calls == ["metadata_only"]



@pytest.mark.parametrize("transition", ("provider_epoch", "core_epoch", "new_session"))
def test_canonical_confirmation_rejects_same_account_session_aba(
    canonical, transition, monkeypatch,
):
    from cifereca.core import source_context

    case = canonical.case
    token = case.broker.prepare_local_materialization(
        case.source,
    ).facts["confirmation_token"]
    if transition == "provider_epoch":
        initial = getattr(case.target, "_epoch", None)
        case.target._epoch = initial + 1 if type(initial) is int else 1
    elif transition == "core_epoch":
        current = source_context.source_context_epoch(case.service)
        monkeypatch.setattr(
            source_context, "source_context_epoch",
            lambda _service: current + 1,
        )
    else:
        # Keep all declared source coordinates but replace the Python session.
        # The one-use plan must bind the old session *identity*.
        import copy
        replacement = copy.copy(case.target)
        case.service._sessions[
            case.service._session_key("kobo", case.target.credential_id)
        ] = replacement
    result = case.broker.materialize_source(case.source, token)
    assert result.ok is False
    assert result.facts["status"] == "source_changed"
    assert canonical.calls == []


def test_empty_primary_kobo_borrower_can_preview_and_confirm_once(canonical):
    """D2: the primary account_id is exactly '', never an implicit alias."""
    case = canonical.case
    account = case.target.accounts[0]
    assert account.is_primary is True
    account.unique_id = ""
    case.source = case.broker._source_id(account)
    canonical.snapshots[0] = _snapshot(case)

    preview = case.broker.prepare_local_materialization(case.source)
    assert preview.ok and preview.facts["execution_path"] == "canonical"
    assert preview.facts["acquisition_authorized"] is False
    assert not canonical.calls
    token = preview.facts["confirmation_token"]
    executed = case.broker.materialize_source(case.source, token)
    assert executed.ok and executed.changed == 1
    assert executed.facts["execution_path"] == "canonical"
    assert canonical.calls == [case.source]
    assert not case.downloaded and not any(case.calls.values())
    replay = case.broker.materialize_source(case.source, token)
    assert not replay.ok and replay.facts["status"] == "confirmation_unavailable"
    assert canonical.calls == [case.source]


@pytest.mark.parametrize("primary,borrower", [
    (False, ""), (True, " "), (True, " borrower"), (True, "borrower "),
])
def test_invalid_or_secondary_blank_borrower_is_rejected_before_provider(
    canonical, primary, borrower,
):
    """Only the exact empty *primary* borrower is a valid empty SourceKey."""
    case = canonical.case
    account = case.target.accounts[0]
    account.is_primary = primary
    account.unique_id = borrower
    case.source = case.broker._source_id(account)
    canonical.snapshots[0] = _snapshot(case)
    preview = case.broker.prepare_local_materialization(case.source)
    assert not preview.ok and preview.facts["status"] == "invalid_source"
    assert "confirmation_token" not in preview.facts
    assert not canonical.calls and not case.downloaded
    assert not any(case.calls.values())


def test_empty_primary_lease_revoked_before_confirm_on_borrower_switch(
    canonical,
):
    case = canonical.case
    account = case.target.accounts[0]
    account.unique_id = ""
    case.source = case.broker._source_id(account)
    canonical.snapshots[0] = _snapshot(case)
    token = case.broker.prepare_local_materialization(
        case.source,
    ).facts["confirmation_token"]
    account.unique_id = "different-borrower"
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok
    assert result.facts["status"] == "unknown_source"
    assert not canonical.calls and not case.downloaded
