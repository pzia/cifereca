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
    "new_session_same_scope", "provider_epoch", "core_epoch",
))
def test_retired_legacy_token_never_authorizes_session_aba(case, switch):
    """The revoked D2 token is unusable even before session changes."""
    initial = _snapshot(case)
    prepared = case.broker.prepare_local_materialization(case.source)
    assert not prepared.ok
    assert prepared.facts["status"] == "canonical_delivery_unavailable"
    assert "confirmation_token" not in prepared.facts
    if switch == "new_session_same_scope":
        case.service._sessions[
            case.service._session_key("kobo", case.target.credential_id)
        ] = copy.copy(case.target)
    elif switch == "provider_epoch":
        case.target._epoch = 1
    else:
        case.service._source_context_epoch += 1
    replay = case.broker.materialize_source(case.source, "a" * 64)
    assert not replay.ok
    assert replay.facts["status"] in {
        "canonical_delivery_unavailable", "source_changed",
    }
    assert not case.downloaded and not any(case.calls.values())
    assert _snapshot(case) == initial


@pytest.mark.parametrize("switch", (
    "new_session_same_scope", "provider_epoch", "core_epoch",
))
def test_partial_abi_never_reads_manifest_to_issue_token(case, monkeypatch, switch):
    """No manifest fetch means no old check-then-act TOCTOU window."""
    def forbidden():
        pytest.fail("W2 must not fetch a manifest or mint a token")
    monkeypatch.setattr(case.target, "backup_manifest", forbidden)
    before = _snapshot(case)
    prepared = case.broker.prepare_local_materialization(case.source)
    assert not prepared.ok
    assert prepared.facts["status"] == "canonical_delivery_unavailable"
    assert "confirmation_token" not in prepared.facts
    assert _snapshot(case) == before
    assert not case.downloaded and not any(case.calls.values())


@pytest.mark.parametrize("mode", ("core_secondary", "provider_secondary"))
def test_secondary_empty_borrower_never_issues_legacy_token(case, mode):
    account = case.target.accounts[0]
    account.unique_id = ""
    case.source = case.broker._source_id(account)
    secondary = replace(account, is_primary=False)
    if mode == "core_secondary":
        case.service.accounts = [
            secondary if x is account else x for x in case.service.accounts
        ]
    case.target._accounts = [secondary]
    before = _snapshot(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] in {
        "invalid_source", "ambiguous_account", "canonical_delivery_unavailable",
    }
    assert "confirmation_token" not in result.facts
    assert not case.downloaded and not any(case.calls.values())
    assert _snapshot(case) == before
