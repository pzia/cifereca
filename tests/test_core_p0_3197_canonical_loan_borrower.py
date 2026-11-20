"""Real Core/v17 borrower grant on the canonical selected ACSM path (#3197)."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.canonical_selected_delivery import complete_selected_delivery
from cifereca.locking import InterProcessLock
from tests.test_paris_borrower_adept_binding import (
    service_with_linked_borrower, confirm_borrower_v16,
)
from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, new_source
from dataclasses import replace


@pytest.mark.parametrize("scenario", [
    "missing", "v15_only", "confirmed", "revoked_during_get",
    "revoked_during_adept", "profile_unready", "profile_inventory_missing",
    "profile_inventory_error", "profile_name_mismatch", "profile_id_mismatch",
])
def test_linked_loan_canonical_adept_uses_exact_grant(
    tmp_path, monkeypatch, scenario,
):
    service, loan = service_with_linked_borrower(tmp_path)
    db = service.storage
    try:
        account = service.accounts[1]
        with db._lock, db._connect() as sql:
            connection, = sql.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id=? AND credential_id=?",
                (account.provider_id, account.credential_id),
            ).fetchone()
        db.provider_account_put({
            "connection_id": connection, "provider_id": account.provider_id,
            "credential_id": account.credential_id, "identity_id": account.identity_id,
            "account_id": account.unique_id, "is_primary": False,
        })
        if scenario == "v15_only":
            seed_historical_adept_binding(
                service, credential_id=account.credential_id,
                account_id=account.unique_id, borrower_identity_id="bob",
                confirmed=True,
            )
        if scenario not in {"missing", "v15_only"}:
            confirm_borrower_v16(service)
        source, snapshot = new_source(
            "alice", provider=account.provider_id, credential=account.credential_id,
            account=account.unique_id, edition=loan.record_id, right=loan.id,
            kind="loan", branch="dilicom",
        )
        offer = replace(snapshot.rights[0], delivery="available",
                        delivery_offer_id="synthetic-offer")
        calls = []

        def revoke():
            db.adept_borrower_grant_delete(
                provider_id=account.provider_id, credential_id=account.credential_id,
                account_id=account.unique_id,
            )

        def candidate(_account, *, target_dir, **_kwargs):
            calls.append("GET")
            path = target_dir / "token.acsm"
            path.write_bytes(
                b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
                b'<format>application/pdf</format></fulfillmentToken>'
            )
            if scenario == "revoked_during_get":
                revoke()
            return path, ""

        session = NS(
            provider_id=account.provider_id, credential_id=account.credential_id,
            accounts=[account], connected=True, _closed=False,
            rights_selected_delivery_candidate=candidate, close=lambda: None,
        )
        service._sessions[service._session_key(
            account.provider_id, account.credential_id)] = session

        def profile(identity):
            assert identity == "bob"
            return "bob-adobe", "configured"

        service.resolve_adept_profile_for_identity = profile
        service._originating_adept_profile_id = (
            lambda identity, _name: db.adept_profile_binding_get("identity", identity)
        )
        def profiles_for_identity(*_args, **_kwargs):
            if scenario == "profile_inventory_error":
                raise RuntimeError("synthetic inventory unavailable")
            if scenario == "profile_inventory_missing":
                return []
            return [{
                "name": ("wrong-adobe" if scenario == "profile_name_mismatch"
                         else "bob-adobe"),
                "profile_id": ("other-stable-id" if scenario == "profile_id_mismatch"
                               else db.adept_profile_binding_get("identity", "bob")),
                "fetch_capable": scenario != "profile_unready",
            }]

        service.adept_profiles_for_identity = profiles_for_identity
        service._adept_profile_row = lambda _name, rows: rows[0] if rows else None

        def runtime(request):
            calls.append("ADEPT")
            assert request["profile"] == "bob-adobe"
            assert request["metadata"]["identity_id"] == "bob"
            target = Path(request["destination"])
            target.write_bytes(BYTES)
            if scenario == "revoked_during_adept":
                revoke()
            return {"ok": True, "profile": "bob-adobe",
                    "artifact": {"path": str(target)}}

        service.execute_acquisition_request = runtime
        path = tmp_path / "state" / "acquisition.lock"
        monkeypatch.setattr(
            "cifereca.core.canonical_selected_delivery.acquisition_lock_file",
            lambda: path,
        )
        monkeypatch.setattr(
            "cifereca.core.canonical_recovery.acquisition_lock_file",
            lambda: path,
        )
        root = tmp_path / "EBooks"
        with InterProcessLock(path) as lock:
            def execute():
                return complete_selected_delivery(
                    service, account, session, source, offer, library_root=root,
                    shared_lock=lock, consent_still_current=lambda: True,
                )
            if scenario == "confirmed":
                result = execute()
                assert result.final_sha256 == SHA
                assert result.path.is_relative_to(root / "alice")
                assert result.adept_receipt_ref.startswith("core-adept:")
            else:
                with pytest.raises(ValueError, match="adept_borrower|adept_profile"):
                    execute()
        expected = {
            "missing": [], "v15_only": [], "confirmed": ["GET", "ADEPT"],
            "revoked_during_get": ["GET"], "revoked_during_adept": ["GET", "ADEPT"],
            "profile_unready": ["GET"],
            "profile_inventory_missing": ["GET"],
            "profile_inventory_error": ["GET"],
            "profile_name_mismatch": ["GET"],
            "profile_id_mismatch": ["GET"],
        }
        assert calls == expected[scenario]
        if scenario in {
            "profile_unready", "profile_inventory_missing",
            "profile_inventory_error", "profile_name_mismatch",
            "profile_id_mismatch",
        }:
            from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt
            assert pending_loan_adept_attempt(db, source) is False
        assert db.placements() == []  # Only the executor may publish.
        if scenario != "confirmed":
            assert not list(root.rglob("*.acsm"))
            assert not list(root.rglob("*.pdf"))
    finally:
        service.close()
