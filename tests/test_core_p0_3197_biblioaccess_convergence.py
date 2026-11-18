"""#3197: actual BiblioAccess producer -> Core A/B in both Sync entry points.

SQLite, source CAS, signed ACSM staging and Core publication are real.
Only upstream CAS/MyLoans, HTTP transport and Adobe fulfillment are synthetic.
"""
from pathlib import Path
import sys

import httpx
import pytest

from cifereca.core.source_sync_consent import set_source_sync_consent
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA
from tests.test_sync_state_contract import _service

PLUGIN = Path(__file__).resolve().parents[1] / "src/plugins/provider-biblioaccess"
for directory in (PLUGIN, PLUGIN / "tests"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
from test_biblioaccess_sso import _canonical_row, _synced_session


@pytest.mark.parametrize("first_opening", [False, True])
@pytest.mark.parametrize("scenario", ["on", "off", "revoked_during_get"])
def test_biblioaccess_core_canonical_convergence(
    tmp_path, monkeypatch, first_opening, scenario,
):
    import biblioaccess_acsm_delivery
    service = _service(tmp_path)
    row = _canonical_row()
    row.due_at_local = "08/11/2099 12:30:00"
    session, _, _ = _synced_session(rows=[row])
    try:
        account, = session.connect()
        account.identity_id = "default"
        db = service.storage
        db.identity_put({
            "identity_id": "default", "label": "Hugues", "directory_name": "hugues",
        })
        connection = db.provider_connection_put({
            "identity_id": account.identity_id, "provider_id": account.provider_id,
            "credential_id": account.credential_id, "enabled": True, "label": "Synthetic",
        })
        db.provider_account_put({
            "connection_id": connection, "identity_id": account.identity_id,
            "provider_id": account.provider_id, "credential_id": account.credential_id,
            "account_id": account.unique_id, "is_primary": True,
        })
        service.accounts = [account]
        service._sessions[service._session_key(
            account.provider_id, account.credential_id,
        )] = session
        for module in ("services", "core.sync_coordination", "core.canonical_selected_offers",
                       "core.convergence_execution", "core.canonical_recovery",
                       "core.canonical_selected_delivery"):
            monkeypatch.setattr("cifereca." + module + ".acquisition_lock_file",
                                lambda: tmp_path / "acquisition.lock")
        service._acquire_active_loan_automatically = lambda *_a, **_kw: pytest.fail(
            "configured source must never fall back to legacy executor"
        )
        service.resolve_adept_profile_for_identity = (
            lambda identity: ("synthetic-adobe", "configured")
        )
        service._originating_adept_profile_id = lambda *_: "synthetic-profile-id"
        service.adept_profiles_for_identity = lambda *_a, **_kw: [
            {"name": "synthetic-adobe", "fetch_capable": True},
        ]
        service._adept_profile_row = lambda _name, rows: rows[0]
        calls = []
        policy = set_source_sync_consent(
            service, account, enabled=scenario != "off", expected_revision=None,
        )

        def remote(request):
            assert request.method == "GET"
            assert request.headers.get("cookie") is None
            calls.append("GET")
            if scenario == "revoked_during_get":
                set_source_sync_consent(
                    service, account, enabled=False,
                    expected_revision=policy["revision"],
                )
            return httpx.Response(
                200, headers={"content-type": "application/vnd.adobe.adept+xml"},
                content=(b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
                         b'fulfillmentType="loan"><resourceItemInfo>'
                         b'<format>application/pdf</format></resourceItemInfo>'
                         b'</fulfillmentToken>'),
            )

        stage = biblioaccess_acsm_delivery.stage_loan_acsm
        monkeypatch.setattr(
            biblioaccess_acsm_delivery, "stage_loan_acsm",
            lambda url, target, **kw: stage(
                url, target, transport=httpx.MockTransport(remote), **kw,
            ),
        )

        def execute(request):
            calls.append("ADEPT")
            assert request["profile"] == "synthetic-adobe"
            assert request["metadata"]["identity_id"] == "default"
            Path(request["destination"]).write_bytes(BYTES)
            return {"ok": True, "profile": "synthetic-adobe",
                    "artifact": {"path": request["destination"]}}

        service.execute_acquisition_request = execute

        def sync(opening):
            if opening:
                result = service.opening_sync()
                return next(item for item in result["identities"]
                            if item["identity_id"] == "default")
            return service.synchronize_account_source(account)

        first = sync(first_opening)
        assert first["canonical_execution"]["downloaded"] == int(scenario == "on")
        if scenario == "on":
            assert calls == ["GET", "ADEPT"]
            placement, = db.placements()
            assert placement["identity_id"] == "default"
            assert placement["artifact_id"] == "sha256:" + SHA
            assert Path(placement["path"]).read_bytes() == BYTES
            assert Path(placement["path"]).parent == service.library_dir() / "hugues" / "Acquisitions"
            second = sync(not first_opening)
            assert second["canonical_execution"]["present_attested"] == 1
            assert second["canonical_execution"]["downloaded"] == 0
            assert calls == ["GET", "ADEPT"]
            assert db.placements() == [placement]
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM origin_links").fetchone() == (2,)
        else:
            assert calls == ([] if scenario == "off" else ["GET"])
            assert db.placements() == []
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM origin_links").fetchone() == (0,)
    finally:
        service.close()
