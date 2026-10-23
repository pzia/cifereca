"""P0 #2975: cached loans may not migrate between borrowers/identities.

Provider reads are fake and cache persists in a real local SQLite database.
No acquisition, ADEPT, HTTP or filesystem rewrite is permitted.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.services import _loan_cache_key
from tests.test_sync_state_contract import _service


PRIVATE = "PRIVATE_SCOPED_BORROWER"


def _account(*, credential, uid, identity="default", is_primary=False):
    return Account(
        display_name="PRIVATE account", unique_id=uid, provider_id="paris",
        credential_id=credential, identity_id=identity, is_primary=is_primary,
    )


def _loan(account, item):
    return Loan(
        id=item, provider="DILICOM", medium="digital",
        record_id=item, title="PRIVATE title " + item,
        source_provider="paris", source_credential=account.credential_id,
        source_account_id=account.unique_id or "primary",
        identity_id=account.identity_id,
    )


def _session(service, credential, loans, calls):
    session = NS(
        capabilities=NS(loans=True),
        loans=lambda account: calls.append((credential, account.identity_id)) or
        list(loans(account)),
    )
    service._sessions[service._session_key("paris", credential)] = session
    return session


def test_colon_coordinates_cannot_share_a_loan_cache_entry(tmp_path):
    service = _service(tmp_path)
    try:
        # Previously both composed "paris:a:b:c:loans" with ':' joins.
        a = _account(credential="a:b", uid="c")
        b = _account(credential="a", uid="b:c")
        assert (f"paris:{a.credential_id}:{a.unique_id}:loans"
                == f"paris:{b.credential_id}:{b.unique_id}:loans")
        assert _loan_cache_key(a) != _loan_cache_key(b)
        calls = []
        _session(service, a.credential_id, lambda _account: [_loan(a, "edition-A")], calls)
        _session(service, b.credential_id, lambda _account: [_loan(b, "edition-B")], calls)
        assert [r.id for r in service._loans_for_account(a, force=True)] == ["edition-A"]
        assert [r.id for r in service._loans_for_account(b, force=True)] == ["edition-B"]
        assert [r.id for r in service._loans_for_account(a)] == ["edition-A"]
        assert [r.id for r in service._loans_for_account(b)] == ["edition-B"]
        assert len(calls) == 2
        assert not service.storage.entitlements()
        assert PRIVATE not in service.diagnostics.text("test")
    finally:
        service.close()


def test_same_borrower_and_credential_in_two_identities_do_not_share_cache(tmp_path):
    service = _service(tmp_path)
    try:
        alice = _account(credential="joint-credential", uid="child", identity="default")
        bob = _account(credential="joint-credential", uid="child", identity="alex")
        assert _loan_cache_key(alice) != _loan_cache_key(bob)
        calls = []
        _session(service, alice.credential_id, lambda account: [
            _loan(account, "PRIVATE-" + account.identity_id)
        ], calls)
        first = service._loans_for_account(alice, force=True)
        second = service._loans_for_account(bob)
        assert first[0].identity_id == "default"
        assert second[0].identity_id == "alex"
        assert first[0].id != second[0].id
        assert len(calls) == 2  # no alias reuse for Bob
        assert service._loans_for_account(alice)[0].id == first[0].id
        assert len(calls) == 2
        assert not service.storage.entitlements()
    finally:
        service.close()


def test_primary_sentinel_and_literal_primary_uid_have_distinct_cache_scope(tmp_path):
    service = _service(tmp_path)
    try:
        main = _account(credential="shared", uid="", is_primary=True)
        literal = _account(credential="shared", uid="primary", is_primary=False)
        assert _loan_cache_key(main) != _loan_cache_key(literal)
        calls = []
        _session(service, "shared", lambda account: [
            _loan(account, "main" if account.unique_id == "" else "literal")
        ], calls)
        assert service._loans_for_account(main, force=True)[0].id == "main"
        assert service._loans_for_account(literal, force=True)[0].id == "literal"
        assert service._loans_for_account(main)[0].id == "main"
        assert service._loans_for_account(literal)[0].id == "literal"
        assert len(calls) == 2
    finally:
        service.close()


@pytest.mark.parametrize("bad_field,bad_value", [
    ("identity_id", "alex"),
    ("source_account_id", "other"),
    ("source_credential", "other-credential"),
    ("source_provider", "kobo"),
    ("id", None),
    ("medium", "alien"),
])
def test_foreign_or_corrupt_cached_row_causes_fresh_read_without_reassignment(
    tmp_path, bad_field, bad_value,
):
    service = _service(tmp_path)
    try:
        account = _account(credential="cred", uid="borrower")
        good = _loan(account, "correct")
        poisoned = asdict(replace(_loan(account, "FOREIGN"), **{bad_field: bad_value}))
        cache_key = _loan_cache_key(account)
        service.storage.cache_put("account", cache_key, [poisoned], ttl_seconds=300)
        reads = []
        _session(service, account.credential_id, lambda _account: [good], reads)
        rows = service._loans_for_account(account)
        assert len(reads) == 1
        assert [row.id for row in rows] == ["correct"]
        assert rows[0].identity_id == "default"
        assert rows[0].source_credential == "cred"
        assert service.storage.cache_get("account", cache_key)[0]["id"] == "correct"
        assert len(reads) == 1
        assert service._loans_for_account(account)[0].id == "correct"
        assert len(reads) == 1
        assert not service.storage.entitlements()
    finally:
        service.close()


def test_old_ambiguous_alias_is_never_used_even_when_it_contains_foreign_loans(tmp_path):
    service = _service(tmp_path)
    try:
        account = _account(credential="cred", uid="reader")
        old = "paris:cred:reader:loans"
        row = asdict(_loan(account, "FOREIGN"))
        row["identity_id"] = "alex"
        service.storage.cache_put("account", old, [row], ttl_seconds=300)
        assert service.storage.cache_get("account", old)[0]["id"] == "FOREIGN"
        calls = []
        _session(service, account.credential_id,
                 lambda _account: [_loan(account, "CURRENT")], calls)
        found = service._loans_for_account(account)
        assert [row.id for row in found] == ["CURRENT"]
        assert len(calls) == 1
        assert service.storage.cache_get("account", _loan_cache_key(account))[0]["id"] == "CURRENT"
        assert "FOREIGN" not in json.dumps([x.id for x in found])
    finally:
        service.close()


@pytest.mark.parametrize("coordinate", ["identity_id", "credential_id", "unique_id"])
def test_invalid_cache_key_coordinate_fails_before_provider_contact(
    tmp_path, coordinate,
):
    service = _service(tmp_path)
    try:
        account = _account(credential="cred", uid="borrower")
        setattr(account, coordinate, "PRIVATE\x00BROKEN")
        with pytest.raises(Exception, match="loan_cache_scope_invalid"):
            service._loans_for_account(account)
        assert not service.storage.entitlements()
    finally:
        service.close()
