"""#4640: descriptor-local KVault producer evidence, without private GETs.

All fixtures are synthetic. The release label alone is not an attested source SHA.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p0_3252_kdrm_canonical_runtime import kdrm_candidate
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup


_SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1",
)


@pytest.mark.parametrize(
    ("producer", "probe", "release", "revision"),
    [
        (None, "absent", "unavailable", "unavailable"),
        (
            {"release": "0.9.8", "contract_revision": "2.3.4",
             "flepub_media_probe": "epub"},
            "epub", "0.9.8", "2.3.4",
        ),
        (
            {"release": "9.7.1", "contract_revision": "2.3.4",
             "flepub_media_probe": "unknown"},
            "unknown", "9.7.1", "2.3.4",
        ),
        (
            {"release": "PRIVATE-URL?token=SECRET",
             "contract_revision": "PRIVATE-SCOPE",
             "flepub_media_probe": "SECRET-FORMAT"},
            "other", "unavailable", "unavailable",
        ),
        ({"release": True, "contract_revision": {}, "flepub_media_probe": True},
         "other", "unavailable", "unavailable"),
    ],
)
def test_k2_descriptor_producer_probe_is_closed_and_never_infers_epub(
    producer, probe, release, revision,
):
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = "unknown"
    candidate["source_format"] = "FLEPUB"
    candidate["format"] = "FLEPUB"
    payload = descriptor(candidate=candidate)
    if producer is not None:
        payload["producer_contract"] = producer
    diagnosis = {}
    result = exact_kobo_offer_id(
        payload, **_SCOPE, kdrm_runtime_ready=True, diagnostic=diagnosis,
    )
    assert result == ""
    assert diagnosis["candidate_kvault_flepub_contract"] == "media_unknown"
    assert diagnosis["candidate_kvault_flepub_producer_probe"] == probe
    assert diagnosis["candidate_kvault_descriptor_release"] == release
    assert diagnosis["candidate_kvault_descriptor_contract"] == revision
    assert diagnosis["candidate_rejection_counts"] == {
        "kdrm_format_pair_unqualified": 1,
    }
    assert "SECRET" not in str(diagnosis)
    assert "http" not in str(diagnosis).lower()


def test_k2_live_normalizer_probe_does_not_change_offer_or_id():
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = "epub"
    candidate["source_format"] = "FLEPUB"
    candidate["format"] = "FLEPUB"
    payload = descriptor(candidate=candidate)
    original = exact_kobo_offer_id(
        payload, **_SCOPE, kdrm_runtime_ready=True,
    )
    payload["producer_contract"] = {
        "release": "0.9.8", "contract_revision": "2.3.4",
        "flepub_media_probe": "unknown",
    }
    diagnosis = {}
    current = exact_kobo_offer_id(
        payload, **_SCOPE, kdrm_runtime_ready=True, diagnostic=diagnosis,
    )
    assert original == current and current.startswith("kobo.runtime:")
    assert diagnosis["candidate_kvault_flepub_producer_probe"] == "unknown"


def test_k2_provider_event_uses_only_descriptor_local_safe_categories(caplog):
    session, vault, _broker, account, posts = setup()
    session.runtime_capabilities = SimpleNamespace(
        acquisition=lambda _p: SimpleNamespace(
            available=True, compatible=True, operational=True,
            modes=("kobo_kdrm",),
        )
    )
    def observed_descriptor(product_id, **options):
        posts.append((product_id, options))
        candidate = kdrm_candidate(product_id)
        candidate["media_format"] = "unknown"
        candidate["source_format"] = "FLEPUB"
        candidate["format"] = "FLEPUB"
        return {
            **descriptor(product=product_id, candidate=candidate),
            "producer_contract": {
                "release": "PRIVATE-TOKEN-SECRET", "contract_revision": "2.3.4",
                "flepub_media_probe": "epub",
            },
        }
    vault.acquisition = observed_descriptor
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            snapshot = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        events = [
            record.getMessage() for record in caplog.records
            if "EVENT kobo selected_offer" in record.getMessage()
        ]
        assert len(events) == 1
        event = events[0]
        assert "candidate_kvault_flepub_producer_probe=epub" in event
        assert "candidate_kvault_descriptor_release=unavailable" in event
        assert "candidate_kvault_descriptor_contract=2.3.4" in event
        assert "outcome=unknown" in event
        assert "PRIVATE-TOKEN-SECRET" not in event
        assert "https://" not in event
    finally:
        session.close()
