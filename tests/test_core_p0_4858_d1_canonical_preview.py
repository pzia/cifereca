"""#4858 D1: a full-ABI Kobo live preview uses only typed A and verified B.

Private synthetic account with 0.7 history; no provider GET, backup read,
legacy permission, or materialization side effect is allowed.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import (
    ProviderRightsScope, ProviderRightsSnapshot, ProviderRightObservation,
)
from cifereca.core import rights_snapshot_read, convergence_verified_presence
from tests.test_source_materialization_confirmation import case, _dump  # noqa: F401


def _rights(account):
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
            provider_revision_id="r1",
            delivery="available", delivery_offer_id="offer-id",
        ),),
        status="success", coverage="partial", branch="purchases",
    )


@pytest.fixture
def typed(case, monkeypatch):
    account = case.target.accounts[0]
    for name in (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate",
    ):
        monkeypatch.setattr(
            case.target, name,
            lambda *_a, **_k: pytest.fail("provider must only be read through typed Core"),
            raising=False,
        )
    observed = [_rights(account)]
    decisions = [NS(
        state="present_attested",
        source=NS(edition_id="PRIVATE-book-1"),
        provider_satisfied=True,
    )]
    monkeypatch.setattr(
        rights_snapshot_read, "read_account_rights_snapshot",
        lambda *_a, **_k: observed[0],
    )
    monkeypatch.setattr(
        convergence_verified_presence, "plan_existing_verified_presence",
        lambda *_a, **_k: NS(decisions=tuple(decisions)),
    )
    def forbid(*_a, **_k):
        pytest.fail("D1 canonical live preview must not read 0.7 backup or GET")
    monkeypatch.setattr(case.target, "backup_manifest", forbid)
    monkeypatch.setattr(case.service.storage, "backup_list", forbid)
    monkeypatch.setattr(case.service.storage, "backup_get", forbid)
    monkeypatch.setattr(case.service.storage, "backup_put", forbid)
    monkeypatch.setattr(case.service, "_execute_kobo_acquisition", forbid)
    return NS(case=case, observed=observed, decisions=decisions, account=account)


def test_live_typed_preview_attests_only_exact_core_b_not_backup(typed):
    case = typed.case
    before = _dump(case)
    report = case.service.kobo_backup_preview(
        "vault-2", refresh_manifest=True, verify_local_files=True,
    )
    assert report["canonical_source"] is True
    assert report["counts"]["already_local_verified"] == 1
    assert report["counts"]["already_local_linked"] == 0
    assert report["counts"]["potentially_missing"] == 0
    assert report["acquisition_authorized"] is False
    assert report["preview_only"] and report["manifest_valid"]
    assert _dump(case) == before
    assert not case.downloaded and not any(case.calls.values())
    public = json.dumps(report)
    for secret in ("PRIVATE", "vault-2", "reader-2", "offer-id"):
        assert secret not in public


@pytest.mark.parametrize("state,reason", [
    ("present_declared", "needs_reconciliation"),
    ("needs_reconciliation", "needs_reconciliation"),
    ("missing_to_download", "potentially_missing"),
    ("pending", "potentially_missing"),
    ("inactive", "not_downloadable"),
    ("skipped_archived", "skipped_archived"),
])
def test_canonical_preview_states_never_inherit_legacy_complete(typed, state, reason):
    typed.decisions[0] = NS(
        state=state, source=NS(edition_id="PRIVATE-book-1"),
        provider_satisfied=False,
    )
    report = typed.case.service.kobo_backup_preview(
        "vault-2", refresh_manifest=True,
    )
    assert report["counts"][reason] == 1
    assert report["counts"]["already_local_verified"] == 0
    assert report["counts"]["already_local_linked"] == 0
    assert report["acquisition_authorized"] is False


def test_canonical_preview_rejects_session_rebind_after_typed_read(typed, monkeypatch):
    case = typed.case
    before = _dump(case)
    original = rights_snapshot_read.read_account_rights_snapshot
    def rebound(*args, **kwargs):
        value = original(*args, **kwargs)
        case.target._epoch = object()
        return value
    monkeypatch.setattr(
        rights_snapshot_read, "read_account_rights_snapshot", rebound,
    )
    from cifereca.providers import ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError, match="canonical_kobo_preview_scope_changed"):
        case.service.kobo_backup_preview("vault-2", refresh_manifest=True)
    assert _dump(case) == before
    assert not case.downloaded


def test_canonical_preview_refuses_failed_or_foreign_provider_a(typed):
    case = typed.case
    old = typed.observed[0]
    typed.observed[0] = ProviderRightsSnapshot(
        scope=old.scope, status="failed",
    )
    from cifereca.providers import ProviderConfigurationError
    with pytest.raises(ProviderConfigurationError, match="canonical_kobo_preview_unavailable"):
        case.service.kobo_backup_preview("vault-2", refresh_manifest=True)
    assert not any(case.calls.values())
