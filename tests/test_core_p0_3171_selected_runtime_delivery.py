"""P0 #3171/#3252: generic Core runtime recipe is ephemeral, scope-bound.

All tests exercise real SQLite and Core Sync, but fake the DRM executor and
supplier I/O. In particular, an offer ID or runtime result is not B proof.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.downloadable_resources import DeliveryCandidate
from tests.test_core_p0_3171_canonical_sync_stage import rig
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3719_source_sync_consent import source, change
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES


def bridge(service, calls, *, revoke=None, foreign=False, duplicate=False):
    def guarded(account, *, admission_current, execute_runtime,
                edition_id, resource_id, kind, branch, selected_offer_id,
                target_dir, explicit_user_action):
        assert explicit_user_action and admission_current() is True
        assert (edition_id, resource_id) == (
            service.source.edition_id, service.source.right_id
        )
        assert resource_id == service.source.right_id
        calls.append("runtime")
        candidate = target_dir / "verified.pdf"
        request = {
            "schema_version": 2,
            "source": {"type": "kobo", "provider": "fake", "request": {
                "url": "https://example.org/fixture"}},
            "format": "pdf", "destination": str(candidate),
        }
        if foreign:
            request["destination"] = str(target_dir.parent / "other.pdf")
        elif duplicate:
            candidate.write_bytes(BYTES)
        else:
            def execute(arg):
                assert arg is request
                candidate.write_bytes(BYTES)
                if revoke:
                    revoke()
                return {"ok": True, "artifact": {"path": str(candidate)}}
            service.execute_acquisition_request = execute
        result = execute_runtime(request)
        return DeliveryCandidate(
            service.resource_snapshot.resources[0].source, selected_offer_id,
            candidate,
        )
    service.session.downloadable_selected_delivery_candidate_runtime_guarded = guarded


def test_generic_runtime_mints_final_receipt_and_is_idempotent(source):
    service, db, root, calls, _broker = source
    change(source, True)
    bridge(service, calls)
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert len(db.placements()) == 1
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert calls == ["offer", "runtime"]
    assert db.entitlements() == []


def test_generic_runtime_denied_when_source_off(source):
    service, db, root, calls, _broker = source
    bridge(service, calls)
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert calls == [] and db.placements() == []


@pytest.mark.parametrize("reason", ["foreign", "occupied", "revoked"])
def test_generic_runtime_refuses_wrong_target_and_revocation_without_B(source, reason):
    service, db, root, calls, _broker = source
    on = change(source, True)

    def revoke():
        change(source, False, on["revision"])

    bridge(service, calls, foreign=reason=="foreign",
           duplicate=reason=="occupied",
           revoke=revoke if reason=="revoked" else None)
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    assert calls == ["offer", "runtime"]
