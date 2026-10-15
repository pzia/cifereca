"""P1 #1346: Kobo opening decision accounts are distinct from cached sessions."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core.sync_coordination import _kobo_opening_decision


def _host(accounts, sessions, *, enabled=True, due=False):
    return NS(
        accounts=accounts, _sessions={str(i): s for i, s in enumerate(sessions)},
        kobo_backup_enabled=lambda: enabled,
        kobo_backup_interval_minutes=lambda: 90,
        _kobo_backup_due=lambda: due,
        sync_source_enabled=lambda account: account.enabled,
        storage=NS(metadata_get=lambda key: None),
    )


def _account(identity, credential, enabled=True, provider="kobo"):
    return NS(identity_id=identity, credential_id=credential,
              provider_id=provider, enabled=enabled)


def _session(credential, *, connected=True, backup=True, provider="kobo"):
    return NS(credential_id=credential, connected=connected,
              provider_id=provider, capabilities=NS(backup=backup))


def test_two_identities_same_credential_count_as_two_accounts_one_session():
    host = _host([_account("a", "c"), _account("b", "c")], [_session("c")], due=True)
    result = _kobo_opening_decision(host)
    assert result["eligible_kobo"] == 1
    assert result["eligible_kobo_accounts"] == 2
    assert result["status"] == "started"


def test_disabled_account_and_other_provider_do_not_count():
    host = _host([_account("a", "c", False), _account("b", "c", True, "paris")],
                 [_session("c")], due=True)
    result = _kobo_opening_decision(host)
    assert result["eligible_kobo_accounts"] == 0
    assert result["status"] == "source_disabled"


def test_unmatched_credential_is_not_eligible():
    host = _host([_account("a", "other")], [_session("c")], due=True)
    assert _kobo_opening_decision(host)["status"] == "source_disabled"


def test_disconnected_or_uncapable_session_never_qualifies():
    for session in (_session("c", connected=False), _session("c", backup=False)):
        result = _kobo_opening_decision(_host([_account("a", "c")], [session]))
        assert result["eligible_kobo_accounts"] == 0
        assert result["status"] in {"no_session", "capability_unavailable"}


def test_fresh_cache_and_disabled_flag_preserve_existing_reason_codes():
    host = _host([_account("a", "c")], [_session("c")])
    assert _kobo_opening_decision(host)["status"] == "fresh_cache"
    host.kobo_backup_enabled = lambda: False
    assert _kobo_opening_decision(host)["status"] == "disabled"


def test_deep_bypasses_freshness_only_not_account_switch():
    host = _host([_account("a", "c", False)], [_session("c")])
    assert _kobo_opening_decision(host, deep=True)["status"] == "source_disabled"
    host.accounts[0].enabled = True
    assert _kobo_opening_decision(host, deep=True)["status"] == "started"


def test_report_does_not_expose_private_account_or_credential_identifiers():
    host = _host([_account("private-profile", "private-credential")],
                 [_session("private-credential")])
    result = _kobo_opening_decision(host)
    assert "private-profile" not in str(result)
    assert "private-credential" not in str(result)
    assert result["eligible_kobo_accounts"] == 1
