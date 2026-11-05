"""P0 #3171/#3252: generic Core runtime recipe is ephemeral, scope-bound.

All tests exercise real SQLite and Core Sync, but fake the DRM executor and
supplier I/O. In particular, an offer ID or runtime result is not B proof.
"""
from __future__ import annotations

from pathlib import Path
import logging

import pytest

from cifereca.artifact_inspection import ArtifactInspection

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


@pytest.mark.parametrize("route", ("skipped", "substituted", "failed_swallowed"))
def test_runtime_guarded_candidate_cannot_substitute_or_skip_runtime_proof(
    source, route,
):
    """Even valid, identical bytes in Core staging are not a DRM receipt."""
    service, db, root, calls, _broker = source
    change(source, True)

    def guarded(_account, *, admission_current, execute_runtime,
                edition_id, resource_id, kind, branch, selected_offer_id,
                target_dir, explicit_user_action):
        assert admission_current() is True and explicit_user_action is True
        calls.append("runtime")
        runtime_path = target_dir / "runtime.pdf"
        forged_path = target_dir / "forged.pdf"
        if route != "skipped":
            def fake_runtime(request):
                assert request["destination"] == str(runtime_path)
                if route == "failed_swallowed":
                    raise RuntimeError("synthetic failed runtime")
                runtime_path.write_bytes(BYTES)
                return {"ok": True, "artifact": {"path": str(runtime_path)}}

            service.execute_acquisition_request = fake_runtime
            request = {
                "schema_version": 2,
                "source": {"type": "kobo", "provider": "synthetic"},
                "format": "pdf", "destination": str(runtime_path),
            }
            if route == "failed_swallowed":
                try:
                    execute_runtime(request)
                except RuntimeError:
                    pass  # A provider must not convert a failed runtime to B.
            else:
                execute_runtime(request)
        forged_path.write_bytes(BYTES)
        return DeliveryCandidate(
            service.resource_snapshot.resources[0].source,
            selected_offer_id, forged_path,
        )

    service.session.downloadable_selected_delivery_candidate_runtime_guarded = guarded
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    assert calls == ["offer", "runtime"]


@pytest.mark.parametrize("tamper", ("replace_inode", "rewrite_in_place"))
def test_runtime_guarded_cannot_swap_bytes_at_witnessed_path(source, tamper):
    """The same candidate path is not proof of the runtime's original bytes."""
    service, db, root, calls, _broker = source
    change(source, True)

    def guarded(_account, *, admission_current, execute_runtime,
                edition_id, resource_id, kind, branch, selected_offer_id,
                target_dir, explicit_user_action):
        assert admission_current() is True and explicit_user_action is True
        calls.append("runtime")
        produced = target_dir / "runtime.pdf"

        def runtime(request):
            assert request["destination"] == str(produced)
            produced.write_bytes(BYTES)
            return {"ok": True, "artifact": {"path": str(produced)}}

        service.execute_acquisition_request = runtime
        execute_runtime({
            "schema_version": 2,
            "source": {"type": "kobo", "provider": "synthetic"},
            "format": "pdf", "destination": str(produced),
        })
        if tamper == "replace_inode":
            replacement = target_dir / "replacement.pdf"
            replacement.write_bytes(BYTES)
            replacement.replace(produced)
        else:
            # Preserve the inode and size while changing the content.
            mutated = bytearray(BYTES)
            mutated[-1] ^= 1
            produced.write_bytes(mutated)
        return DeliveryCandidate(
            service.resource_snapshot.resources[0].source,
            selected_offer_id, produced,
        )

    service.session.downloadable_selected_delivery_candidate_runtime_guarded = guarded
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    assert calls == ["offer", "runtime"]


def test_runtime_capable_session_can_use_guarded_direct_offer_without_drm(
    source,
):
    """A direct HTTP/PDF offer must not be mislabeled a failed DRM runtime."""
    service, db, root, calls, _broker = source
    change(source, True)
    routes = []

    def requires_runtime(account, *, edition_id, resource_id, kind,
                         branch, selected_offer_id):
        routes.append(selected_offer_id)
        assert account is service.account
        return False

    def direct(_account, *, admission_current, edition_id, resource_id,
               kind, branch, selected_offer_id, target_dir,
               explicit_user_action):
        assert admission_current() is True
        assert explicit_user_action is True
        calls.append("direct")
        file = target_dir / "direct.pdf"
        file.write_bytes(BYTES)
        return DeliveryCandidate(
            service.resource_snapshot.resources[0].source,
            selected_offer_id, file,
        )

    def unexpected_runtime(*_args, **_kwargs):
        pytest.fail("a direct offer must not execute Core DRM runtime")

    service.session.downloadable_selected_delivery_requires_runtime = requires_runtime
    service.session.downloadable_selected_delivery_candidate_guarded = direct
    service.session.downloadable_selected_delivery_candidate_runtime_guarded = unexpected_runtime
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert len(db.placements()) == 1
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES
    assert calls == ["offer", "direct"]
    assert len(routes) == 1
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert calls == ["offer", "direct"]


@pytest.mark.parametrize("route", ("invalid", "exception", "unguarded"))
def test_runtime_route_classifier_never_downgrades_to_legacy_candidate(
    source, route,
):
    service, db, root, calls, _broker = source
    change(source, True)

    def classify(*_args, **_kwargs):
        if route == "exception":
            raise RuntimeError("synthetic private classifier failure")
        return [] if route == "invalid" else False

    def no_runtime(*_args, **_kwargs):
        pytest.fail("unqualified runtime route must not acquire bytes")

    service.session.downloadable_selected_delivery_requires_runtime = classify
    service.session.downloadable_selected_delivery_candidate_runtime_guarded = no_runtime
    if route != "unguarded":
        service.session.downloadable_selected_delivery_candidate_guarded = no_runtime
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    assert calls == ["offer"]


def test_exact_runtime_route_still_requires_core_execution_proof(source):
    service, db, root, calls, _broker = source
    change(source, True)

    service.session.downloadable_selected_delivery_requires_runtime = (
        lambda *_args, **_kwargs: True
    )

    def skip_runtime(_account, *, admission_current, execute_runtime,
                     edition_id, resource_id, kind, branch, selected_offer_id,
                     target_dir, explicit_user_action):
        assert admission_current() is True
        calls.append("runtime")
        forged = target_dir / "unattested.pdf"
        forged.write_bytes(BYTES)
        return DeliveryCandidate(
            service.resource_snapshot.resources[0].source,
            selected_offer_id, forged,
        )

    service.session.downloadable_selected_delivery_candidate_runtime_guarded = skip_runtime
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert db.placements() == [] and db.entitlements() == []
    assert calls == ["offer", "runtime"]



@pytest.mark.parametrize("method", (
    "downloadable_selected_delivery_candidate_guarded",
    "downloadable_selected_delivery_candidate_runtime_guarded",
))
def test_declared_broken_guarded_abi_never_falls_back_to_unchecked_get(
    source, method,
):
    """A malformed live-consent opt-in must not select the legacy candidate."""
    service, db, root, calls, _broker = source
    change(source, True)
    assert callable(service.session.downloadable_selected_delivery_candidate)
    setattr(service.session, method, "malformed-provider-method")

    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    assert calls == ["offer"]


def test_runtime_success_with_rejected_final_bytes_reports_kdrm_verdict(
    source, monkeypatch, caplog,
):
    # #3252: two real Kobo KDRM candidates reported a successful runtime but
    # failed the later final validator. A zero runtime exit is not B proof.
    service, db, root, calls, _broker = source
    change(source, True)
    bridge(service, calls)

    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.inspect_artifact",
        lambda _path, *, strict_structure: ArtifactInspection(
            "pdf", True, True, "synthetic_protected",
        ),
    )
    with caplog.at_level(
        logging.WARNING, logger="cifereca.core.canonical_selected_delivery",
    ):
        result = synchronize_account_source(service, service.account)

    execution = result["canonical_execution"]
    assert execution["downloaded"] == 0 and execution["failed"] == 1
    assert db.placements() == [] and db.entitlements() == []
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))
    assert calls == ["offer", "runtime"]
    diagnoses = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("TRACE canonical delivery final rejected ")
    ]
    assert len(diagnoses) == 1
    detail = diagnoses[0]
    assert "code=canonical_delivery_final_invalid" in detail
    assert "reader=downloadable_selected_delivery_candidate_runtime_guarded" in detail
    assert "runtime_guarded=True" in detail
    assert "expected_kind=pdf" in detail
    assert "inspection_valid=True" in detail
    assert "inspection_format=pdf" in detail
    assert "inspection_protected=True" in detail
    assert "inspection_reason=synthetic_protected" in detail
    assert "catalog_valid=True" in detail
    # Even detailed diagnostic mode never includes provider-private recipe
    # URLs, borrower identity or the exact temporary file path.
    assert "https://" not in detail
    assert str(root) not in detail
