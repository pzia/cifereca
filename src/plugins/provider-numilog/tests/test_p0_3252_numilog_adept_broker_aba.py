"""#3252: a same-owner Core broker swap cannot publish a stale ADEPT result.

All broker interactions are synthetic and local. No ACSM GET, external ADEPT
runtime, remote account or SQLite/OriginLink is accessed.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_numilog_owned_enrichment_identity_race_p0 import (
    MutableBroker, make_session,
)


@pytest.mark.parametrize("race", ["broker_replaced", "duplicate_owner", "identity_reassigned"])
@pytest.mark.parametrize("prebound_owner", [False, True])
def test_numilog_adept_import_result_refused_after_owner_binding_race(
    tmp_path, race, prebound_owner,
):
    session, broker, _row = make_session()
    produced = tmp_path / "synthetic.epub"
    produced.write_bytes(b"synthetic-file-for-broker-only-test")
    calls = []

    def import_acsm(path, **kwargs):
        calls.append((path, kwargs))
        if race == "broker_replaced":
            newer = MutableBroker()
            newer.scope = broker.scope
            newer.owner = broker.owner  # A->A, label unchanged, broker changed
            session.identity_broker = newer
        elif race == "duplicate_owner":
            original = broker.accounts
            broker.accounts = lambda identity_id=None: original() * 2
        else:
            broker.owner = "identity-bob"
        return {"status": "complete", "path": str(produced)}

    session.acquisition_broker = NS(import_acsm=import_acsm)
    try:
        with pytest.raises(ProviderCapabilityError, match="rattachement du compte modifié"):
            session._fulfill_acsm(
                tmp_path / "synthetic.acsm",
                identity_id="identity-alice" if prebound_owner else None,
            )
        assert len(calls) == 1
        assert calls[0][1]["identity_id"] == "identity-alice"
        assert calls[0][1]["provider_id"] == "numilog"
    finally:
        session.close()


def test_numilog_stable_owner_still_returns_the_same_adept_artifact(tmp_path):
    session, broker, _row = make_session()
    produced = tmp_path / "synthetic.epub"
    produced.write_bytes(b"synthetic-file-for-broker-only-test")
    calls = []

    def import_acsm(path, **kwargs):
        calls.append(kwargs)
        return {"status": "complete", "path": str(produced)}

    session.acquisition_broker = NS(import_acsm=import_acsm)
    try:
        final_path, _url = session._fulfill_acsm(
            tmp_path / "synthetic.acsm", identity_id="identity-alice",
        )
        assert final_path == produced
        assert calls == [{"identity_id": "identity-alice",
                          "provider_id": "numilog", "title": None}]
        assert broker.calls >= 3
    finally:
        session.close()


def test_numilog_duplicate_owner_rejected_before_starting_adept_import(tmp_path):
    session, broker, _row = make_session()
    original = broker.accounts
    broker.accounts = lambda identity_id=None: original() * 2
    calls = []
    session.acquisition_broker = NS(import_acsm=lambda *_a, **_kw: calls.append("import"))
    try:
        with pytest.raises(ProviderCapabilityError):
            session._fulfill_acsm(tmp_path / "synthetic.acsm")
        assert calls == []
    finally:
        session.close()
