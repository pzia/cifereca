"""#4988 native Search occurrence receipt, independent of web/offer evidence."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace

import pytest

from cifereca.core.borrow_for_identity_native_occurrence import (
    native_borrow_occurrence_document, stamp_native_borrow_occurrences,
)
from cifereca.core.notice_c_private_search_web import capture_authenticated_search_call
from cifereca.models import SearchResult
from cifereca.storage import Storage
from tests.test_core_p0_6397_authenticated_web_choices import Host


def original(account):
    return SearchResult(
        id="native-row", record_id="record-1", docbase="fixture",
        medium="digital", title="Fixture", author=None, type="book",
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="fixture",
        source_provider=account.provider_id,
        source_credential=account.credential_id,
        source_account_id=account.unique_id,
    )


def setup(db):
    host = Host(db)
    account = host.account("alice")
    session = host._sessions[(account.provider_id, account.credential_id)]
    call = capture_authenticated_search_call(host, session, "alice")
    assert call is not None
    return host, account, session, call


def document(host, row):
    return native_borrow_occurrence_document(host, row, owner="alice")


def test_original_authenticated_occurrence_has_no_web_or_offer_requirement(tmp_path):
    with closing(Storage(tmp_path / "native.sqlite")) as db:
        host, account, _, call = setup(db)
        row = original(account)
        stamp_native_borrow_occurrences(host, call, [row])
        doc = document(host, row)
        assert doc is not None and doc.identity_id == "alice"
        assert doc.account_id == account.unique_id
        assert document(host, replace(row)) is None
        assert document(host, deepcopy(row)) is None
        assert native_borrow_occurrence_document(host, row, owner="bob") is None
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("change", [
    "record", "scenario", "revision", "account", "mirror", "session",
    "epoch", "owner", "disabled", "duplicates", "wrong_container",
])
def test_native_occurrence_revokes_without_rebinding(tmp_path, change):
    with closing(Storage(tmp_path / "native.sqlite")) as db:
        host, account, session, call = setup(db)
        row = original(account)
        stamp_native_borrow_occurrences(host, call, [row])
        assert document(host, row) is not None
        if change == "record":
            row.record_id = "record-2"
        elif change == "scenario":
            row.scenario = "other"
        elif change == "revision":
            row.revision_id = "r2"
        elif change == "account":
            row.source_account_id = "wrong"
        elif change == "mirror":
            row.metadata["catalogue_access"] = "shared_read_only"
        elif change == "session":
            session.connected = False
        elif change == "epoch":
            session._epoch += 1
        elif change == "owner":
            host.identities["alice"] = replace_identity = object()
            assert replace_identity is not call.scope.identity
        elif change == "disabled":
            host.enabled = False
        elif change == "duplicates":
            stamp_native_borrow_occurrences(host, call, [row, replace(row)])
        else:
            stamp_native_borrow_occurrences(host, call, (row,))
        assert document(host, row) is None
        assert db.entitlements() == db.acquisitions() == db.placements() == []
