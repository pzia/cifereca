"""#1905: corrupt unrelated Numilog accounts must not block consent revocation."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.numilog_materialization_consent import (
    _binding, _source_accounts, mode, policy_key,
)
from tests.test_core_p0_numilog_auto_materialization import _authorize, _setup


class CorruptCredential:
    """Bad account data must not leak through a source-policy exception."""
    def __str__(self):
        raise RuntimeError(
            "https://private.invalid/auth?token=SECRET_NEVER_ECHO"
        )


@pytest.mark.parametrize("position", ["first", "last"])
def test_broken_unrelated_account_cannot_suppress_valid_source_consent(
    tmp_path, position,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        corrupt = NS(
            provider_id="numilog", identity_id="default",
            credential_id=CorruptCredential(), unique_id="unrelated",
        )
        if position == "first":
            service.accounts.insert(0, corrupt)
        else:
            service.accounts.append(corrupt)
        # Source enumeration is isolated. Exact binding still validates all
        # durable owner, credential and live-session evidence.
        assert _source_accounts(broker, sid) == [account]
        bound = _binding(broker, sid)
        assert bound is not None and bound[0] is account and bound[1] is session
        _authorize(broker, sid)
        assert mode(broker, sid) == "on_start"
        assert service.storage.metadata_get(policy_key(sid)) == "on_start:v1"
        # Revocation must also work after the real account disconnects:
        # it cannot depend on a fresh authenticated provider session.
        session.connected = False
        assert broker.revoke_numilog_materialization_consent(sid) == "manual"
        assert mode(broker, sid) == "manual"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        service.close()


def test_genuinely_ambiguous_matching_accounts_remain_fail_closed(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        service.accounts.append(replace(account))
        assert len(_source_accounts(broker, sid)) == 2
        assert _binding(broker, sid) is None
        assert not broker.prepare_numilog_materialization_consent(sid).ok
        with pytest.raises(ValueError, match="unknown_numilog_source"):
            broker.revoke_numilog_materialization_consent(sid)
        assert mode(broker, sid) == "manual"
        assert service.storage.metadata_get(policy_key(sid)) is None
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("bad_id", [
    None, "", "https://private.invalid/?token=SECRET", "sync:" + "z" * 24,
    "sync:" + "a" * 23, ("sync:" + "a" * 24) + "\nSECRET",
])
def test_invalid_or_unregistered_source_never_changes_numilog_consent(
    tmp_path, bad_id,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        assert _source_accounts(broker, bad_id) == []
        assert _binding(broker, bad_id) is None
        with pytest.raises(ValueError, match="unknown_numilog_source"):
            broker.revoke_numilog_materialization_consent(bad_id)
        assert service.storage.metadata_get(policy_key(sid)) is None
        assert calls == []
    finally:
        service.close()


def test_many_unrelated_providers_cannot_disable_numilog_consent_revocation(
    tmp_path,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        service.accounts[:0] = [
            NS(provider_id="other-provider", identity_id="default",
               credential_id=f"unrelated-{i}", unique_id=f"other-{i}")
            for i in range(270)
        ]
        assert _source_accounts(broker, sid) == [account]
        _authorize(broker, sid)
        assert mode(broker, sid) == "on_start"
        session.connected = False
        assert broker.revoke_numilog_materialization_consent(sid) == "manual"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        service.close()
