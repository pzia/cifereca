"""#2835: Kobo-specific source/consent coordinates on real Core convergence.

The supplier callback and receipts are synthetic. No actual Kobo session,
device, DRM, network, or mutation of any existing library.
"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.source_acquisition_consent import (
    consent_current_in_transaction, read_source_acquisition_consent,
)
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register
from tests.test_core_p0_3171_canonical_sync_stage import refs
from tests.test_core_p0_3171_kobo_consent_aba import _explicit_cas
from tests.test_core_p0_3171_selected_candidate_finalization import (
    rig, _isolate_delivery_lock,
)
from tests.test_p1_4496_canonical_epub_readable_names import _epub


@pytest.fixture
def kobo(rig):
    service, db, root = rig
    account = replace(service.account, provider_id="kobo")
    service.account = account
    service.accounts = [account]
    service.session.provider_id = "kobo"
    service.session.accounts = [account]
    service._sessions = {
        service._session_key("kobo", account.credential_id): service.session,
    }
    service.source, service.snapshot = new_source(
        "A", provider="kobo", credential=account.credential_id,
        account=account.unique_id, branch="personal_manifest",
        edition="purchase-17", right="purchase-17",
    )
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available",
        delivery_offer_id="kobo.descriptor:" + "a" * 64,
    ),))
    register(db, "A", "kobo", account.credential_id, account=account.unique_id)
    service._canonical_persisted_consent = (
        lambda observed: read_source_acquisition_consent(service, observed)
    )
    service._canonical_publication_consent_current = (
        lambda conn, source: consent_current_in_transaction(conn, source)
    )
    _explicit_cas(db, service.source, revision="a" * 32)
    return service, db, root


@pytest.mark.parametrize("previous_delivery", [True, False])
def test_kobo_exact_fxl_presence_or_authorized_epub_delivery(kobo, previous_delivery):
    service, db, root = kobo
    payload = _epub()
    digest = sha256(payload).hexdigest()
    directory = root / "A" / "Acquisitions"
    directory.mkdir(parents=True)
    fxl = directory / "Existing.fxl.kepub.epub"
    fxl.write_bytes(payload)
    if previous_delivery:
        publish_completed_acquisition(
            db, library_root=root, path=fxl, source=service.source,
            snapshot=service.snapshot, final_sha256=digest,
            delivery_receipt_ref="synthetic-kobo-existing-delivery",
            expected_source_policy_revision="a" * 32,
            transaction_guard=lambda conn: consent_current_in_transaction(conn, service.source),
        )
    assert LocalCatalog(db, root).scan().cataloged == 1
    calls = []
    delivered = directory / "New.epub"

    def receive(key, offer):
        assert not previous_delivery, "exact historical placement needs no GET"
        assert key == service.source
        calls.append(offer.delivery_offer_id)
        assert not delivered.exists()
        delivered.write_bytes(payload)
        return CompletedDelivery(
            key, offer.delivery_offer_id, delivered, digest,
            "synthetic-kobo-authorized-delivery",
        )

    service._canonical_right_delivery = receive
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == int(not previous_delivery)
    assert first["canonical_execution"]["present_attested"] == int(previous_delivery)
    assert len(calls) == int(not previous_delivery)
    assert refs(db)[1] == 1
    assert len(db.placements()) == (1 if previous_delivery else 2)
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    assert second["canonical_execution"]["downloaded"] == 0
    assert len(calls) == int(not previous_delivery)
    assert refs(db)[1] == 1
    assert fxl.read_bytes() == payload
    if not previous_delivery:
        assert delivered.read_bytes() == payload
