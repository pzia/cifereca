"""P0 #4858 D2: one-use manual Kobo confirmation is a session lease.

A legacy manifest fingerprint is not permission to acquire on a reconnect,
even if the provider returns byte-for-byte identical upstream rights. Tests
use the live SQLite broker, three real source bindings, synthetic transport,
and no network or historical filesystem writes on rejection.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from tests.test_source_materialization_confirmation import case  # noqa: F401


def _snapshot(case):
    storage = case.service.storage
    with storage._lock, storage._connect() as db:
        return {
            table: db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
            for table in (
                "backup_items", "entitlements", "acquisition_ledger",
                "artifacts", "placements", "source_references", "origin_links",
            )
        }


@pytest.mark.parametrize("switch", (
    "new_session_same_scope",
    "provider_epoch",
    "core_epoch",
))
def test_legacy_token_is_revoked_after_session_aba_even_with_same_manifest(
    case, switch,
):
    plan = case.broker.prepare_local_materialization(case.source)
    assert plan.ok
    token = plan.facts["confirmation_token"]
    assert case.calls == {"vault-1": 0, "vault-2": 1, "vault-3": 0}
    before = _snapshot(case)
    if switch == "new_session_same_scope":
        replacement = copy.copy(case.target)
        assert replacement is not case.target
        assert replacement.accounts[0].unique_id == case.target.accounts[0].unique_id
        case.service._sessions[
            case.service._session_key("kobo", case.target.credential_id)
        ] = replacement
    elif switch == "provider_epoch":
        case.target._epoch = 1
    else:
        case.service._source_context_epoch += 1

    result = case.broker.materialize_source(case.source, token)
    assert not result.ok
    assert result.facts["status"] == "source_changed"
    assert "PRIVATE" not in repr(result)
    assert case.calls == {"vault-1": 0, "vault-2": 1, "vault-3": 0}
    assert not case.downloaded
    assert _snapshot(case) == before


@pytest.mark.parametrize("switch", (
    "new_session_same_scope",
    "provider_epoch",
    "core_epoch",
))
def test_reconnect_during_legacy_rights_preview_does_not_issue_token(
    case, monkeypatch, switch,
):
    original = case.target.backup_manifest

    def switch_during_manifest():
        result = original()
        if switch == "new_session_same_scope":
            case.service._sessions[
                case.service._session_key("kobo", case.target.credential_id)
            ] = copy.copy(case.target)
        elif switch == "provider_epoch":
            case.target._epoch = 1
        else:
            case.service._source_context_epoch += 1
        return result

    monkeypatch.setattr(case.target, "backup_manifest", switch_during_manifest)
    before = _snapshot(case)
    plan = case.broker.prepare_local_materialization(case.source)
    assert not plan.ok
    assert plan.facts["status"] == "source_changed"
    assert "confirmation_token" not in plan.facts
    assert not case.downloaded
    assert _snapshot(case) == before


@pytest.mark.parametrize("mode", ("core_secondary", "provider_secondary"))
def test_secondary_empty_kobo_borrower_cannot_prepare_or_confirm_manual_get(
    case, mode,
):
    account = case.target.accounts[0]
    assert account.is_primary is True
    account.unique_id = ""
    case.source = case.broker._source_id(account)

    secondary = replace(account, is_primary=False)
    if mode == "core_secondary":
        # Preserve the canonical source_id even if is_primary is switched,
        # demonstrating why account ID alone cannot validate it.
        case.service.accounts = [
            secondary if x is account else x for x in case.service.accounts
        ]
        case.target._accounts = [secondary]
    else:
        case.target._accounts = [secondary]

    before = _snapshot(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] in {"invalid_source", "ambiguous_account"}
    assert "confirmation_token" not in result.facts
    assert not case.downloaded
    assert case.calls == {"vault-1": 0, "vault-2": 0, "vault-3": 0}
    assert _snapshot(case) == before
