"""P0 #3252: selected Kobo preflight trace reflects actual observed phase.

Every request is a synthetic Vault fake. No file GET, Kobo API, ContentKeys,
signed candidate URL or live account is ever logged by these tests.
"""
from __future__ import annotations

import logging

import pytest

from cifereca_kobo_vault import KoboVaultError
from tests.test_p0_3252_kdrm_canonical_runtime import ready, selected
from tests.test_p1_3252_kobo_selected_delivery_offer import setup


def _events(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if "EVENT kobo selected_offer " in record.getMessage()
    ]


def test_successful_pinned_kdrm_trace_is_not_a_download_receipt(caplog):
    session, vault, _broker, account, _ = setup()
    ready(session, vault)
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        lines = _events(caplog)
        assert len(lines) == 1
        assert "product_id=book-1" in lines[0]
        assert "reason=kdrm_recipe_attested" in lines[0]
        assert "outcome=available" in lines[0]
        assert "candidate_count=1" in lines[0]
        assert "runtime_ready=True" in lines[0]
        assert "delegated_resolver_called=True" in lines[0]
        assert "downloaded" not in lines[0]
    finally:
        session.close()


def test_missing_token_scope_is_a_local_refusal_not_an_upstream_403(caplog):
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)
    session.supports_acquisition_delegation = False
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            offer = selected(session, account, "book-1")
        assert offer.delivery == "unknown"
        assert calls == [("book-1", "descriptor")]  # zero delegated POST
        lines = _events(caplog)
        assert len(lines) == 1
        assert "reason=delegation_scope_unattested" in lines[0]
        assert "outcome=unknown" in lines[0]
        assert "delegated_resolver_called=True" in lines[0]
        assert "delegated_http_403" not in lines[0]
    finally:
        session.close()


def test_observed_delegated_http_403_is_distinct_from_missing_scope(caplog):
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)
    original = vault.acquisition
    attempts = []
    def forbidden(product, **options):
        if options.get("mode") == "delegated":
            attempts.append(product)
            raise KoboVaultError(
                "synthetic delegated HTTP refusal",
                code="forbidden", status_code=403,
            )
        return original(product, **options)
    vault.acquisition = forbidden
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            offer = selected(session, account, "book-1")
        assert offer.delivery == "unknown"
        assert calls == [("book-1", "descriptor")]
        assert attempts == ["book-1"]  # one upstream delegated attempt
        lines = _events(caplog)
        assert len(lines) == 1
        assert "reason=delegated_http_403" in lines[0]
        assert "delegated_resolver_called=True" in lines[0]
    finally:
        session.close()


def test_runtime_unavailable_is_not_mistaken_for_delegation_403(caplog):
    session, vault, _broker, account, _ = setup()
    calls = ready(session, vault)
    session.runtime_capabilities = None
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            offer = selected(session, account, "book-1")
        assert offer.delivery == "unknown"
        assert calls == [("book-1", "descriptor")]
        lines = _events(caplog)
        assert len(lines) == 1
        assert "reason=descriptor_candidate_unqualified" in lines[0]
        assert "kdrm_candidate_seen=True" in lines[0]
        assert "runtime_ready=False" in lines[0]
        assert "delegated_resolver_called=False" in lines[0]
    finally:
        session.close()


def test_plain_offer_stays_non_delegated_and_no_token_in_trace(caplog):
    session, vault, _broker, account, posts = setup()
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        lines = _events(caplog)
        assert len(lines) == 1
        assert "reason=descriptor_offer_attested" in lines[0]
        assert "delegated_resolver_called=False" in lines[0]
        assert "kdrm_candidate_seen=False" in lines[0]
        assert "SYNTHETIC_PRIVATE_SIGNATURE_DO_NOT_EXPORT" not in lines[0]
        assert "https://" not in lines[0]
    finally:
        session.close()


def test_subsequent_book_keeps_prior_403_quarantined_without_new_http(caplog):
    session, vault, _broker, account, _ = setup()
    ready(session, vault)
    posts = []
    original = vault.acquisition
    def forbidden(product, **options):
        if options.get("mode") == "delegated":
            posts.append(product)
            raise KoboVaultError(
                "synthetic previous 403", code="forbidden", status_code=403,
            )
        return original(product, **options)
    vault.acquisition = forbidden
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            first = selected(session, account, "book-1")
            second = selected(session, account, "book-1")
        assert first.delivery == second.delivery == "unknown"
        assert posts == ["book-1"]  # the next selected offer never POSTs
        lines = _events(caplog)
        assert len(lines) == 2
        assert "reason=delegated_http_403" in lines[0]
        assert "reason=delegation_quarantined_after_previous_403" in lines[1]
        assert "http_status=403" not in lines[1]
        assert "delegated_resolver_called=True" in lines[1]
    finally:
        session.close()


@pytest.mark.parametrize("code", ("revision_mismatch", "conflict"))
def test_descriptor_409_preserves_only_closed_cause_in_selected_offer_trace(caplog, code):
    session, vault, _broker, account, _posts = setup()
    calls = []
    secret = "SYNTHETIC_PRIVATE_409_MESSAGE"

    def refused(product, **options):
        calls.append((product, options.get("mode")))
        raise KoboVaultError(
            "upstream private " + secret, code=code, status_code=409,
        )

    vault.acquisition = refused
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            with pytest.raises(KoboVaultError):
                selected(session, account, "book-1")
        assert calls == [("book-1", "descriptor")]
        lines = _events(caplog)
        assert len(lines) == 1
        assert "phase=descriptor outcome=error" in lines[0]
        assert "http_status=409" in lines[0]
        assert f"error_code={code}" in lines[0]
        assert secret not in lines[0]
        assert "https://" not in lines[0]
    finally:
        session.close()


def test_malformed_409_code_does_not_mask_provider_error(caplog):
    session, vault, _broker, account, _posts = setup()
    def invalid_code(_product, **_options):
        raise KoboVaultError("PRIVATE_ERROR_TEXT", code=[], status_code=409)
    vault.acquisition = invalid_code
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            with pytest.raises(KoboVaultError):
                selected(session, account, "book-1")
        lines = _events(caplog)
        assert len(lines) == 1
        assert "http_status=409 error_code=not_applicable" in lines[0]
        assert "PRIVATE_ERROR_TEXT" not in lines[0]
    finally:
        session.close()
