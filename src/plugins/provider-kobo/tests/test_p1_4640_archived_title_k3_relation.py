"""#4640: Kobo archived purchase titles and K3 pending title/URL relation.

All data synthetic. No real Kobo credentials, external traffic,
content GET, archive mutation, or arbitrary candidate preference.
"""
import logging
from copy import deepcopy

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p0_3252_kdrm_canonical_runtime import ready, selected
from tests.test_p1_4640_k2_k3_normalized_selected_execution import (
    SCOPE, _unknown_flepub,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup


def _lines(caplog, prefix):
    return [
        rec.getMessage() for rec in caplog.records
        if rec.getMessage().startswith(prefix)
    ]


def test_manifest_archive_is_nonfailure_notice_with_title_and_zero_descriptor(
    caplog,
):
    session, vault, _broker, account, posts = setup()
    title = "Livre K1 synthétique\nFORGED ERROR never separate line"
    vault.rows[0].update(title=title, archived=True, downloadable=False)
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            observed = session.rights_observation_snapshot(account)
        right = observed.exact("book-1", "book-1", "purchase")
        assert right and right.delivery == "unavailable"
        assert right.delivery_unavailable_reason == "archived"
        assert not right.delivery_offer_id
        assert posts == []  # no POST, no GET candidate

        messages = _lines(caplog, "EVENT kobo archived_purchase ")
        assert len(messages) == 1
        assert "product_id=book-1" in messages[0]
        assert "title=" + repr(title) in messages[0]
        assert "non_failure=true" in messages[0]
        assert "reason=manifest_archived" in messages[0]
        assert len(messages[0].splitlines()) == 1
        assert not any(
            rec.levelno >= logging.ERROR and
            "archived_purchase" in rec.getMessage()
            for rec in caplog.records
        )
    finally:
        session.close()


def test_unarchived_failed_delivery_cannot_be_falsely_marked_archived(caplog):
    session, vault, _broker, account, posts = setup()
    vault.rows[0].update(archived=False)
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            observed = session.rights_observation_snapshot(account)
        right = observed.rights[0]
        assert right.delivery == "unknown"
        assert right.delivery_unavailable_reason == ""
        assert not _lines(caplog, "EVENT kobo archived_purchase ")
        assert not posts
    finally:
        session.close()


@pytest.mark.parametrize(("second_url", "expected_relation"), [
    (
        "https://cdn.kobo.com/first.kepub?signature=OTHER_SYNTHETIC",
        "same_host_path_different_query_or_fragment",
    ),
    (
        "https://cdn.kobo.com/second.kepub?signature=OTHER_SYNTHETIC",
        "same_host_different_paths",
    ),
    (
        "https://files.kobo.com/first.kepub?signature=OTHER_SYNTHETIC",
        "different_hosts",
    ),
])
def test_k3_structural_relation_does_not_authorize_a_candidate(
    second_url, expected_relation,
):
    first = _unknown_flepub()
    first["url"] = (
        "https://cdn.kobo.com/first.kepub?signature=FIRST_SYNTHETIC"
    )
    second = deepcopy(first)
    second["url"] = second_url
    envelope = descriptor(candidate=first)
    envelope["acquisition"]["candidates"] = [first, second]
    diag = {}
    assert exact_kobo_offer_id(envelope, **SCOPE, diagnostic=diag) == ""
    assert diag["candidate_url_relation"] == expected_relation
    assert diag["candidate_route_comparison"] == "same_route_distinct_urls"
    assert diag["eligible_candidate_count"] == 2
    assert "https://" not in str(diag)
    assert "SYNTHETIC" not in str(diag)


def test_k3_ambiguous_title_and_relation_are_info_without_content_get(
    caplog,
):
    session, vault, _broker, account, posts = setup()
    ready(session, vault)
    vault.rows[0]["title"] = "Livre K3 — autre édition"
    first = _unknown_flepub()
    second = deepcopy(first)
    second["url"] = "https://cdn.kobo.com/k3-other?signature=SECRET"
    original = vault.acquisition

    def k3(product, **kw):
        data = original(product, **kw)
        data["acquisition"]["candidates"] = [first, second]
        return data

    vault.acquisition = k3
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            offer = selected(session, account, "book-1")
        assert offer.delivery == "unknown"
        assert offer.delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        notice = _lines(caplog, "EVENT kobo ambiguous_candidates ")
        assert len(notice) == 1
        assert "title='Livre K3 — autre édition'" in notice[0]
        assert "candidate_count=2 outcome=pending non_failure=true" in notice[0]
        selection = _lines(caplog, "EVENT kobo selected_offer ")
        assert len(selection) == 1
        assert "candidate_url_relation=" in selection[0]
        assert "SECRET" not in caplog.text
    finally:
        session.close()
