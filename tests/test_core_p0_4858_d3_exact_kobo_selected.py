"""#4858 D3: exactly one attested Kobo edition through the single Core A->B executor.

Real source/account/Core session fixture, synthetic provider A and a captured
canonical B executor. All legacy backup reads/writes and provider GETs fail.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca import cli
from cifereca.core import canonical_kobo_selected as selected_impl
from cifereca.core import (
    canonical_sync_stage, rights_snapshot_read, sync_coordination,
)
from cifereca.core.source_sync_consent import Policy
from cifereca.plugin_api import (
    ProviderRightsScope, ProviderRightsSnapshot, ProviderRightObservation,
)
from tests.test_source_materialization_confirmation import case  # noqa: F401


STAMP = "2026-11-07T06:00:00+00:00"
_NAMES = (
    "downloaded", "recovered", "present_attested", "pending", "blocked",
    "failed", "cancelled", "skipped_archived", "inactive",
)


def _snapshot(account, *, ambiguous=False):
    scope = ProviderRightsScope(
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    rights = [
        ProviderRightObservation(
            edition_id="book-1", right_id="right-1", kind="purchase",
            state="unknown", evidence_kind="provider_response",
            evidence_ref="synthetic-provider", observed_at=STAMP,
            delivery="unknown", provider_revision_id="r1",
        ),
        ProviderRightObservation(
            edition_id="book-2", right_id="right-2", kind="purchase",
            state="active", evidence_kind="provider_response",
            evidence_ref="synthetic-provider", observed_at=STAMP,
            delivery="unknown", provider_revision_id="r1",
        ),
    ]
    if ambiguous:
        rights.append(ProviderRightObservation(
            edition_id="book-1", right_id="borrow-1", kind="loan",
            state="unknown", evidence_kind="provider_response",
            evidence_ref="synthetic-provider", observed_at=STAMP,
            delivery="unknown", provider_revision_id="r1",
        ))
    return ProviderRightsSnapshot(
        scope=scope, rights=tuple(rights),
        status="partial", coverage="partial", branch="purchases",
    )


@pytest.fixture
def rig(case, monkeypatch):
    service = case.service
    account = case.target.accounts[0]
    consent = [Policy("on", "a" * 32)]
    a = [_snapshot(account)]
    calls = {"reads": [], "persisted": [], "executed": []}

    monkeypatch.setattr(
        selected_impl, "acquisition_lock_file",
        lambda: case.root / "canonical-selected.lock",
    )
    # Canonical executor checks its own real lock; the unit fixture captures
    # what is handed to it while the same global lease is held.
    monkeypatch.setattr(
        service, "sync_source_enabled", lambda row: row is account,
    )
    monkeypatch.setattr(
        service.storage, "provider_scope_account_bound",
        lambda **scope: (scope["identity_id"] == account.identity_id
                         and scope["credential_id"] == account.credential_id
                         and scope["account_id"] == account.unique_id),
    )
    monkeypatch.setattr(
        selected_impl, "read_policy", lambda *_a: consent[0],
    )
    for name in (
        "downloadable_resource_snapshot",
        "downloadable_selected_offer_snapshot",
        "downloadable_selected_delivery_candidate",
    ):
        monkeypatch.setattr(case.target, name, lambda *_args, **_kw:
                            pytest.fail("No legacy or unguarded GET"), raising=False)
    def rights(_service, selected, *, cancelled):
        calls["reads"].append(selected)
        return a[0]

    def persist(storage, snapshot, *, context_current):
        assert storage is service.storage and context_current()
        calls["persisted"].append(snapshot)
        return 1

    def execute(_service, selected, snapshots, **kwargs):
        assert _service is service
        assert selected is account
        assert kwargs["sync_lease"]._handle is not None
        calls["executed"].append(snapshots)
        return {name: int(name == "downloaded") for name in _NAMES}

    monkeypatch.setattr(
        rights_snapshot_read, "read_account_rights_snapshot", rights,
    )
    monkeypatch.setattr(
        canonical_sync_stage, "persist_attested_source_references", persist,
    )
    monkeypatch.setattr(
        sync_coordination, "_run_observed_canonical_convergence", execute,
    )
    for name in ("backup_get", "backup_list", "backup_put"):
        monkeypatch.setattr(
            service.storage, name, lambda *_a, **_k: pytest.fail(
                "Selected Core A->B must never read/write 0.7 backup",
            ),
        )
    return NS(service=service, account=account, credential=account.credential_id,
              consent=consent, a=a, calls=calls, case=case)


def _run(rig, edition="book-1", **kwargs):
    return selected_impl.synchronize_selected_kobo_edition(
        rig.service, credential_id=rig.credential, edition_id=edition,
        explicit_user_action=True, **kwargs,
    )


def test_selected_delivery_only_one_exact_A_right_and_no_legacy_get(rig):
    result = _run(rig)
    assert result["status"] == "downloaded" and result["changed"] == 1
    assert result["counts"]["downloaded"] == 1
    assert result["legacy_backup_mutations"] == 0
    assert result["canonical_only"] is True
    assert len(rig.calls["reads"]) == 1
    assert len(rig.calls["persisted"]) == 1
    assert len(rig.calls["executed"]) == 1
    assert [r.edition_id for r in rig.calls["executed"][0][0].rights] == ["book-1"]
    assert [r.edition_id for r in rig.calls["persisted"][0].rights] == ["book-1"]
    assert not rig.case.downloaded
    public = json.dumps(result)
    for secret in ("book-1", rig.credential, "PRIVATE", "reader-2"):
        assert secret not in public


def test_selected_other_edition_never_runs_sibling_get(rig):
    assert _run(rig, "book-2")["ok"]
    assert [r.edition_id for r in rig.calls["executed"][0][0].rights] == ["book-2"]
    assert all(row.edition_id != "book-1"
               for row in rig.calls["persisted"][0].rights)


@pytest.mark.parametrize("edition,ambiguity,code", [
    ("not-a-book", False, "selected_edition_unavailable"),
    ("book-1", True, "selected_edition_ambiguous"),
])
def test_missing_or_ambiguous_right_never_publishes_A_or_attempts_B(
    rig, edition, ambiguity, code,
):
    rig.a[0] = _snapshot(rig.account, ambiguous=ambiguity)
    with pytest.raises(selected_impl.SelectedKoboRejected, match=code):
        _run(rig, edition)
    assert not rig.calls["persisted"] and not rig.calls["executed"]


def test_source_off_or_explicit_action_absent_is_zero_provider_io(rig):
    with pytest.raises(selected_impl.SelectedKoboRejected, match="invalid_selected_request"):
        selected_impl.synchronize_selected_kobo_edition(
            rig.service, credential_id=rig.credential,
            edition_id="book-1", explicit_user_action=False,
        )
    rig.consent[0] = Policy("off", "b" * 32)
    with pytest.raises(
        selected_impl.SelectedKoboRejected, match="canonical_source_consent_required"
    ):
        _run(rig)
    assert not rig.calls["reads"] and not rig.calls["persisted"]


@pytest.mark.parametrize("race", ("consent_revoked", "provider_epoch", "borrower_rebound"))
def test_rebind_or_revoke_after_A_disallows_B(rig, monkeypatch, race):
    def source_read(_service, selected, *, cancelled):
        rig.calls["reads"].append(selected)
        if race == "consent_revoked":
            rig.consent[0] = Policy("off", "b" * 32)
        elif race == "provider_epoch":
            rig.case.target._epoch = object()
        else:
            rig.case.target.accounts[0].unique_id = "other-borrower"
        return rig.a[0]
    monkeypatch.setattr(
        rights_snapshot_read, "read_account_rights_snapshot", source_read,
    )
    with pytest.raises(selected_impl.SelectedKoboRejected, match="selected_source_changed"):
        _run(rig)
    assert not rig.calls["persisted"] and not rig.calls["executed"]


def test_outcome_cannot_claim_success_if_executor_is_missing_or_broad(
    rig, monkeypatch,
):
    monkeypatch.setattr(
        sync_coordination, "_run_observed_canonical_convergence",
        lambda *_a, **_k: {name: (2 if name == "downloaded" else 0)
                          for name in _NAMES},
    )
    with pytest.raises(selected_impl.SelectedKoboRejected,
                       match="selected_delivery_unavailable"):
        _run(rig)


def test_cli_requires_confirm_before_core_or_provider_start(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.CiferecaCore, "open", lambda **_k: pytest.fail("Core must stay closed"),
    )
    assert cli.main(["kobo-sync-edition", "--credential", "vault",
                     "--item", "book"]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "explicit_edition_confirmation_required"
    assert output.err == ""


def test_cli_connects_only_exact_credential_and_reports_closed_counters(
    monkeypatch, capsys,
):
    calls = []
    service = NS(
        connect_kobo_selected_edition=lambda credential: calls.append(("connect", credential)),
        synchronize_kobo_selected_edition=lambda credential, edition, *,
        explicit_user_action: (
            calls.append(("sync", credential, edition, explicit_user_action))
            or {"status": "present_attested", "ok": True, "changed": 0}
        ),
    )
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_k: NS(library=service, close=lambda: calls.append(("close",))),
    )
    assert cli.main(["kobo-sync-edition", "--credential", "vault",
                     "--item", "book", "--confirm"]) == 0
    assert calls == [
        ("connect", "vault"), ("sync", "vault", "book", True), ("close",),
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "present_attested"
