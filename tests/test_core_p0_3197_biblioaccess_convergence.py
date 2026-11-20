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
@pytest.mark.parametrize("scenario", ["on", "off", "unset", "revoked_during_get",
                                      "historical_attention", "other_loan_attention",
                                      "adept_failed", "adept_raised", "receipt_failed"])
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
            "canonical producer must never fall back to legacy executor"
        )
        service.resolve_adept_profile_for_identity = (
            lambda identity: ("synthetic-adobe", "configured")
        )
        service._originating_adept_profile_id = lambda *_: "synthetic-profile-id"
        service.adept_profiles_for_identity = lambda *_a, **_kw: [
            {"name": "synthetic-adobe", "fetch_capable": True},
        ]
        service._adept_profile_row = lambda _name, rows: rows[0]
        # Exercise real Core negative preflight on the installed service.
        from types import SimpleNamespace as NS
        manager = NS(registry_generation=3)
        fresh_runtime = NS(preflight_acsm_profile=lambda name: name == "synthetic-adobe")
        service._plugin_manager = manager
        service._required_plugin_service = lambda pid: (
            fresh_runtime if pid == "runtime.kobo-adept" else None
        )
        calls = []
        policy = (None if scenario == "unset" else set_source_sync_consent(
            service, account, enabled=scenario != "off", expected_revision=None,
        ))
        expected_download = scenario in {"on", "other_loan_attention"}
        historic_receipt = None
        if scenario in {"historical_attention", "other_loan_attention"}:
            historic_id = db.acquisition_put({
                "acquisition_id": "test-historical-attention",
                "identity_id": account.identity_id, "provider_id": account.provider_id,
                "credential_id": account.credential_id, "account_id": account.unique_id,
                "item_id": row.book_id,
                "status": "attention_required", "strategy": "loan_auto",
                "executor": "kobo-adept",
                "attention_code": "adept_license_fulfilled_by_other_user",
                "metadata": {"provider_loan_id": (
                    "biblioaccess:1409:10001" if scenario == "historical_attention"
                    else "biblioaccess:1409:other"
                )},
            })
            historic_receipt, = db.acquisitions(acquisition_id=historic_id)

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
            if scenario == "adept_failed":
                return {"ok": False}
            if scenario == "adept_raised":
                raise RuntimeError("synthetic runtime interruption")
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

        if scenario == "receipt_failed":
            from cifereca.core import convergence_execution as engine

            def unavailable(*_a, **_kw):
                raise RuntimeError("synthetic checkpoint interruption")

            checkpoint = engine.record_completed_delivery_for_recovery
            monkeypatch.setattr(engine, "record_completed_delivery_for_recovery", unavailable)
        first = sync(first_opening)
        assert first.get("canonical_execution", {}).get("downloaded", 0) == int(expected_download)
        if scenario == "receipt_failed":
            monkeypatch.setattr(engine, "record_completed_delivery_for_recovery", checkpoint)
        blocked_retry = scenario in {"adept_failed", "adept_raised", "receipt_failed"}
        if blocked_retry:
            assert first["canonical_execution"]["failed"] == 1
            # The hold is durable SQLite metadata, not a transient worker
            # variable or inference from an ADEPT error string.
            from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt
            from cifereca.core.source_convergence import SourceKey
            observed = session.downloadable_resource_snapshot(account)
            resource, = observed.resources
            key = SourceKey(
                observed.scope, resource.source.edition_id,
                resource.source.resource_id, "loan", observed.branch,
            )
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM metadata WHERE key LIKE ?",
                                   ("canonical.adept_attempt.v1:%",)).fetchone() == (1,)
            assert pending_loan_adept_attempt(db, key)
            assert sync(not first_opening)["canonical_execution"]["blocked"] == 1
            assert calls == ["GET", "ADEPT"]

            # A manually recovered historical journal may contain real bytes,
            # but it is not a completed canonical intake. Reproduce that
            # persisted state without issuing another real provider request.
            # An unresolved canonical hold must neither disappear nor turn
            # a legacy completion into a new provider-origin publication.
            legacy_file = tmp_path / "legacy-manual-recovery.pdf"
            legacy_file.write_bytes(BYTES)
            recovered_id = db.acquisition_put({
                "acquisition_id": "test-legacy-manual-recovery",
                "identity_id": account.identity_id, "provider_id": account.provider_id,
                "credential_id": account.credential_id, "account_id": account.unique_id,
                "item_id": row.book_id, "status": "complete",
                "strategy": "loan_auto", "executor": "kobo-adept",
                "metadata": {
                    "provider_loan_id": key.right_id,
                    "path": str(legacy_file), "sha256": SHA,
                },
            })
            old_recovery = db.acquisitions(acquisition_id=recovered_id)
            assert len(old_recovery) == 1
            recovered = sync(first_opening)["canonical_execution"]
            assert recovered["blocked"] == 1
            assert recovered["downloaded"] == recovered["present_attested"] == 0
            assert pending_loan_adept_attempt(db, key)
            assert calls == ["GET", "ADEPT"]
            assert db.acquisitions(acquisition_id=recovered_id) == old_recovery
            assert legacy_file.read_bytes() == BYTES
        if scenario == "unset":
            from cifereca.core.source_sync_consent import source_sync_consent
            assert source_sync_consent(service, account)["configured"] is False
            assert first["canonical_rights"]["observed"] == 1
            assert first["canonical_rights"]["present_attested"] == 0
            if not first_opening:
                assert first["canonical_rights"]["pending"] == 1
        if scenario == "historical_attention":
            assert first["canonical_execution"]["blocked"] == 1
            assert sync(not first_opening)["canonical_execution"]["blocked"] == 1
        if expected_download:
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
                assert sql.execute("SELECT count(*) FROM metadata WHERE key LIKE ?",
                                   ("canonical.adept_attempt.v1:%",)).fetchone() == (0,)
                assert sql.execute("SELECT count(*) FROM origin_links").fetchone() == (2,)
        else:
            assert calls == (["GET", "ADEPT"] if blocked_retry else
                             ["GET"] if scenario == "revoked_during_get" else [])
            assert db.placements() == []
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM origin_links").fetchone() == (0,)
        if historic_receipt is not None:
            assert db.acquisitions(acquisition_id=historic_id) == [historic_receipt]
    finally:
        service.close()
