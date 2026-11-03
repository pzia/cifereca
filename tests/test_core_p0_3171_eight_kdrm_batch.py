"""P0 #3171/#3252: eight selected KDRM runtime deliveries converge in one Core Sync.

Real SQLite v14, source-wide persisted consent, Core-selected offers,
ephemeral runtime callback, physical SHA/OriginLinks, retry and idempotence.
Synthetic PDF bytes and a fake runtime; not a real Kobo/DRM acceptance.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.downloadable_resources import (
    DeliveryCandidate, DownloadCapability,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3719_source_sync_consent import source, change


def test_eight_kdrm_runtime_candidates_retry_one_failed_and_then_zero_get(source):
    service, storage, root, calls, _broker = source
    assert change(source, True)["enabled"] is True

    baseline = service.resource_snapshot
    first = baseline.resources[0]
    items = tuple(replace(
        first,
        source=replace(
            first.source,
            edition_id=f"kdrm-edition-{index:02d}",
            resource_id=f"kdrm-item-{index:02d}",
        ),
        evidence_ref=f"private-kobo-evidence-{index:02d}",
        capability=DownloadCapability("unknown"),
        right=None,
    ) for index in range(8))
    service.resource_snapshot = replace(
        baseline, resources=items, status="partial",
        coverage="partial", pagination_complete=False,
    )
    runtime_calls: list[str] = []
    failed_once: set[str] = set()

    def guarded(_account, *, admission_current, execute_runtime, edition_id,
                resource_id, kind, branch, selected_offer_id,
                target_dir, explicit_user_action):
        assert admission_current() is True and explicit_user_action is True
        assert kind == "purchase" and branch == baseline.branch
        assert selected_offer_id == "offer-" + resource_id
        row = next(item for item in items
                   if item.source.edition_id == edition_id
                   and item.source.resource_id == resource_id)
        runtime_calls.append(resource_id)
        destination = target_dir / (resource_id + ".pdf")
        payload = BYTES + resource_id.encode("ascii")

        def fake_runtime(request):
            assert request["destination"] == str(destination)
            if resource_id == "kdrm-item-03" and resource_id not in failed_once:
                failed_once.add(resource_id)
                raise RuntimeError("synthetic interrupted DRM execution")
            destination.write_bytes(payload)
            return {"ok": True, "artifact": {"path": str(destination)}}

        service.execute_acquisition_request = fake_runtime
        execute_runtime({
            "schema_version": 2,
            "source": {"type": "kobo", "provider": "synthetic-runtime"},
            "destination": str(destination), "format": "pdf",
        })
        return DeliveryCandidate(row.source, selected_offer_id, destination)

    service.session.downloadable_selected_delivery_candidate_runtime_guarded = guarded

    first_sync = synchronize_account_source(service, service.account)["canonical_execution"]
    assert first_sync["downloaded"] == 7
    assert first_sync["failed"] == 1
    assert first_sync["pending"] == first_sync["blocked"] == 0
    assert len(runtime_calls) == calls.count("offer") == 8
    assert len(storage.placements()) == 7 and refs(storage)[1] == 7
    assert storage.entitlements() == []

    second_sync = synchronize_account_source(service, service.account)["canonical_execution"]
    assert second_sync["downloaded"] == 1
    assert second_sync["present_attested"] == 7
    assert second_sync["failed"] == second_sync["pending"] == 0
    assert len(runtime_calls) == calls.count("offer") == 9
    assert runtime_calls[-1] == "kdrm-item-03"

    placements = storage.placements()
    assert len(placements) == refs(storage)[1] == 8
    assert len({p["path"] for p in placements}) == 8
    assert all(
        Path(p["path"]).parent == root / "A" / "Acquisitions"
        and Path(p["path"]).read_bytes().startswith(BYTES)
        for p in placements
    )
    assert not list((root / "A" / ".canonical-delivery").glob("run-*"))

    third_sync = synchronize_account_source(service, service.account)["canonical_execution"]
    assert third_sync["present_attested"] == 8
    assert third_sync["downloaded"] == third_sync["failed"] == third_sync["pending"] == 0
    assert len(runtime_calls) == calls.count("offer") == 9
    assert storage.placements() == placements
    assert refs(storage)[1] == 8
