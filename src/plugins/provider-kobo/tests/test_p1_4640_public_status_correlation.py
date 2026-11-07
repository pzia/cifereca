"""#4640: bounded public status correlation, never a producer attestation.

Offline K1/K2/K3 fixtures. Do not change KVault, hit real networks, call
status again during offer preflight, or infer an executable right.
"""
from copy import deepcopy
import logging

import pytest

from tests.test_p0_3252_kdrm_canonical_runtime import kdrm_candidate
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup


@pytest.mark.parametrize("case", ["K1", "K2", "K3"])
@pytest.mark.parametrize(
    ("release", "api", "expected_release", "expected_api"),
    [
        ("0.9.8", "2.3", "0.9.8", "2.3"),
        ("0.9.8?secret=PRIVATE_STATUS_TOKEN", "2.3;PRIVATE_API_TOKEN",
         "unavailable", "unavailable"),
    ],
)
def test_pending_selected_offer_logs_only_bounded_status_at_connect(
    case, release, api, expected_release, expected_api, caplog,
):
    session, vault, _broker, account, posts = setup()
    # In production these are populated only by the existing connect()
    # public /status request; this fixture does not perform a new call.
    session.vault_release = release
    session.api_version = api
    if case == "K1":
        payload = descriptor()
        other = deepcopy(payload["acquisition"]["candidates"][0])
        other["url"] = "https://cdn.kobo.com/second?token=PRIVATE_K1"
        payload["acquisition"]["candidates"].append(other)
        expected_selection = "multiple_eligible_candidates"
    else:
        first = kdrm_candidate("book-1")
        first["media_format"] = "unknown"
        first["source_format"] = "FLEPUB"
        first["format"] = "PRIVATE_K2_K3_ALIAS"
        payload = descriptor(candidate=first)
        if case == "K3":
            other = deepcopy(first)
            other["url"] = "https://cdn.kobo.com/other?token=PRIVATE_K3"
            payload["acquisition"]["candidates"].append(other)
        expected_selection = "no_eligible_candidates"

    def observed_descriptor(product_id, **options):
        posts.append((product_id, options))
        return payload

    vault.acquisition = observed_descriptor
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            snapshot = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        # Only the pre-existing scoped inventory read and descriptor POST.
        assert vault.calls == 1
        events = [
            record.getMessage() for record in caplog.records
            if "EVENT kobo selected_offer" in record.getMessage()
        ]
        assert len(events) == 1
        event = events[0]
        assert f"candidate_selection={expected_selection}" in event
        assert (
            f"kvault_status_release_at_connect={expected_release}"
            in event
        )
        assert f"kvault_status_api_at_connect={expected_api}" in event
        assert "delegated_resolver_called=False" in event
        for secret in (
            "PRIVATE_STATUS_TOKEN", "PRIVATE_API_TOKEN", "PRIVATE_K1",
            "PRIVATE_K2_K3_ALIAS", "PRIVATE_K3", "https://", "token=",
        ):
            assert secret not in event
    finally:
        session.close()


def test_k2_status_absent_does_not_claim_worker_revision(caplog):
    session, vault, _broker, account, posts = setup()
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = "unknown"
    candidate["source_format"] = "FLEPUB"
    vault.acquisition = lambda product_id, **kwargs: (
        posts.append((product_id, kwargs))
        or descriptor(candidate=candidate)
    )
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            snapshot = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        assert snapshot.rights[0].delivery == "unknown"
        events = [
            row.getMessage() for row in caplog.records
            if "EVENT kobo selected_offer" in row.getMessage()
        ]
        assert len(events) == 1
        assert "kvault_status_release_at_connect=unavailable" in events[0]
        assert "kvault_status_api_at_connect=unavailable" in events[0]
        assert "descriptor_release=" not in events[0]
        assert len(posts) == 1
        assert vault.calls == 1
    finally:
        session.close()
