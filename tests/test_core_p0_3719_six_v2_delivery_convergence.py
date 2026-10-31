"""#3719/#3171: six resources, three attested B + three uncertain GETs.

Real SQLite v14, Core source Sync, Core-selected offers, Core staging, SHA,
OriginLinks and restart. All supplier callbacks and PDF bytes are synthetic.
This does NOT certify the six real Numilog EPUB deliveries.
"""
from dataclasses import replace

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.source_convergence import SourceKey
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.downloadable_resources import (
    DeliveryCandidate, DownloadCapability,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, register,
)
from tests.test_core_p0_3171_canonical_sync_stage import rig
from tests.test_core_p0_3719_v2_execution import v2


def test_six_v2_resources_three_attested_three_get_then_no_second_get(v2):
    service, db, root, _original_calls = v2
    baseline = service.resource_snapshot
    initial = baseline.resources[0]
    resources = tuple(replace(
        initial,
        source=replace(
            initial.source,
            edition_id=f"edition-{index}",
            resource_id=f"resource-{index}",
        ),
        capability=DownloadCapability("unknown"),
        evidence_ref=f"authenticated-private-evidence-{index}",
        right=None,
    ) for index in range(6))
    service.resource_snapshot = replace(
        baseline, resources=resources, status="partial",
        coverage="partial", pagination_complete=False,
    )
    keys = tuple(SourceKey(
        row.source.scope, row.source.edition_id,
        row.source.resource_id, row.source.kind, row.source.branch,
    ) for row in resources)
    service._canonical_persisted_consent = lambda _account: frozenset(keys)

    # A is the exact provenance contract. Three already downloaded files are
    # published via B, not adopted on title/ISBN or on an arbitrary SHA.
    for index in range(3):
        path = root / "A" / f"received-before-{index}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        right = replace(
            service.snapshot.rights[0],
            edition_id=keys[index].edition_id,
            right_id=keys[index].right_id,
        )
        snapshot = replace(service.snapshot, rights=(right,))
        published = publish_completed_acquisition(
            db, library_root=root, path=path, source=keys[index],
            snapshot=snapshot, final_sha256=SHA,
            delivery_receipt_ref=f"attested-earlier-receipt-{index}",
        )
        assert published.changed

    # An identical book in another Core profile is not local proof for A.
    register(db, "B", "foreign-provider", "foreign-card",
             account="foreign-reader")
    foreign = root / "B" / "same-content.pdf"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(BYTES)
    foreign_placement = db.placement_put({
        "artifact_id": "sha256:" + SHA, "path": str(foreign),
        "identity_id": "B", "provider_id": "foreign-provider",
        "source_item_id": "foreign-book", "state": "present",
    })

    # An ambiguous historical legacy operation is neither a current
    # OriginLink nor a veto on an otherwise authorized selected offer.
    for index in range(3, 6):
        db.acquisition_put({
            "acquisition_id": f"historical-unverified-{index}",
            "provider_id": "seller-a", "credential_id": "card-a",
            "identity_id": "A", "account_id": "buyer-a",
            "item_id": keys[index].edition_id,
            "status": "attention_required",
            "metadata": {"schema": "legacy-operation",
                         "status": "needs_reconciliation"},
        })

    calls = []
    def selected(_account, *, edition_id, resource_id, kind, branch,
                 explicit_user_action):
        assert explicit_user_action is True
        assert kind == "purchase" and branch == baseline.branch
        row = next(x for x in resources
                   if x.source.edition_id == edition_id
                   and x.source.resource_id == resource_id)
        assert row.source.edition_id in {f"edition-{i}" for i in range(3, 6)}
        calls.append(("offer", edition_id))
        return replace(
            service.resource_snapshot,
            resources=(replace(
                row, capability=DownloadCapability("available", "selected-" + edition_id),
            ),),
            status="partial", coverage="partial", pagination_complete=False,
        )

    def candidate(_account, *, edition_id, resource_id, kind, branch,
                  selected_offer_id, target_dir, explicit_user_action):
        assert explicit_user_action is True
        row = next(x for x in resources
                   if x.source.edition_id == edition_id
                   and x.source.resource_id == resource_id)
        assert selected_offer_id == "selected-" + edition_id
        path = target_dir / (edition_id + ".pdf")
        path.write_bytes(BYTES)
        calls.append(("GET", edition_id))
        return DeliveryCandidate(row.source, selected_offer_id, path)

    service.session.downloadable_selected_offer_snapshot = selected
    service.session.downloadable_selected_delivery_candidate = candidate
    before_legacy = {row["acquisition_id"]: row
                     for row in db.acquisitions()
                     if row["acquisition_id"].startswith("historical-unverified-")}

    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 3
    assert first["canonical_execution"]["present_attested"] == 3
    assert first["canonical_execution"]["failed"] == 0
    assert first["canonical_execution"]["blocked"] == 0
    assert first["canonical_execution"]["pending"] == 0
    assert calls == [entry for index in range(3, 6)
                    for entry in (("offer", f"edition-{index}"),
                                  ("GET", f"edition-{index}"))]
    with db._lock, db._connect() as sql:
        links = sql.execute(
            "SELECT source_reference_id,identity_id,artifact_id "
            "FROM origin_links WHERE origin_kind='provider'"
        ).fetchall()
    assert len(links) == 6
    assert all(x[1] == "A" and x[2] == "sha256:" + SHA for x in links)
    assert len([p for p in db.placements() if p["identity_id"] == "A"]) == 6
    assert db.placement_get(foreign_placement)["identity_id"] == "B"
    assert foreign.read_bytes() == BYTES
    assert all((root / "A").rglob("*.pdf"))
    assert {row["acquisition_id"]: row
            for row in db.acquisitions()
            if row["acquisition_id"].startswith("historical-unverified-")} == before_legacy

    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 6
    assert second["canonical_execution"]["downloaded"] == 0
    assert second["canonical_execution"]["pending"] == 0
    assert len(calls) == 6  # three selected offers and three GET, only once
    assert db.placement_get(foreign_placement)["identity_id"] == "B"
    assert foreign.read_bytes() == BYTES
    assert len([p for p in db.placements() if p["identity_id"] == "A"]) == 6
