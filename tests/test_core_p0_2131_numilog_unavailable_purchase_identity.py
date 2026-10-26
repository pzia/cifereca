"""P0 #2131: a purchased edition needs provenance even without download rights.

All Numilog and ADEPT operations are mocked; only the startup SQLite
checkpoint and local entitlement mirror are exercised.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize("bad_manifest", [
    "invalid_id",
    "invalid_record",
    "duplicate",
])
def test_malformed_owned_but_not_downloadable_purchase_fails_closed_before_mirror(
    tmp_path, monkeypatch, bad_manifest,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        valid = replace(_row(account.credential_id, 0), downloadable=False)
        if bad_manifest == "invalid_id":
            manifest = [replace(valid, id="private-broken-purchase")]
        elif bad_manifest == "invalid_record":
            manifest = [replace(valid, record_id="private-broken-edition")]
        else:
            manifest = [valid, valid]
        session.library = lambda: manifest
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.mirror_numilog_manifest_rights",
            lambda *_args, **_kwargs: pytest.fail("no rights mirroring on incomplete purchase proof"),
        )
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_args, **_kwargs: pytest.fail("not downloadable; no acquisition"),
        )
        outcome = execute(broker, account.identity_id, "startup")
        assert not outcome.ok
        assert outcome.facts["status"] == "failed"
        assert outcome.facts["downloaded"] == outcome.facts["attempted"] == 0
        assert outcome.facts["failed"] == 1
        receipt = broker.numilog_materialization_run_status(sid)
        assert receipt["status"] == "failed"
        assert receipt["diagnostic"]["cause_phase"] == "manifest_fetch"
        assert receipt["diagnostic"]["code"] == "manifest_invalid"
        assert service.storage.entitlements(provider_id="numilog") == []
        assert calls == []
    finally:
        service.close()


def test_valid_owned_not_downloadable_purchase_stays_visible_as_right_without_download(
    tmp_path, monkeypatch,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        valid = replace(_row(account.credential_id, 0), downloadable=False)
        session.library = lambda: [valid]
        monkeypatch.setattr(
            service, "acquire_direct_item",
            lambda *_args, **_kwargs: pytest.fail("unavailable editions must not download"),
        )
        outcome = execute(broker, account.identity_id, "startup")
        # A valid owned right without a deliverable EPUB remains known,
        # but cannot make the local-materialization run 'complete'.
        assert not outcome.ok and outcome.facts["status"] == "blocked"
        assert outcome.facts["blocked"] == 1
        assert outcome.facts["attempted"] == outcome.facts["downloaded"] == 0
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == 1
        assert rights[0]["item_id"] == valid.record_id
        assert rights[0]["entitlement_type"] == "owned"
        assert rights[0]["metadata"]["source"] == "numilog.authenticated_library"
        assert calls == []
    finally:
        service.close()
