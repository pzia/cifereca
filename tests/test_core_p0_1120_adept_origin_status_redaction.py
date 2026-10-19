"""P0 #1120: source status strings from return origin never carry private text.

An exact borrower identity and synthetic acquisition are set up offline.
No Adobe return, PNB POST, network request, or local file mutation.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.return_adept_orchestrator import _public_origin_status
from cifereca.plugin_api import AttentionRequired
from tests.test_core_0984_adept_first_paris_reconcile import setup


PRIVATE = "https://private.invalid/adept/return?auth=NEVER_ECHO"


class Hostile:
    def __bool__(self):
        raise AssertionError("Must not coerce provider status")

    def __str__(self):
        raise AssertionError("Must not stringify provider status")

    def __eq__(self, _other):
        raise AssertionError("Must not compare provider status")


@pytest.mark.parametrize("raw", [PRIVATE, Hostile(), {"url": PRIVATE}, True, 93, ["ready"]])
def test_private_origin_status_is_rejected_without_coercion(raw):
    assert _public_origin_status(raw, default="adept_return_binding_required") == (
        "adept_return_binding_required"
    )


@pytest.mark.parametrize(("origin", "expected", "retry"), [
    ({"status": PRIVATE}, "adept_return_binding_required", "resolve_identity"),
    ({"status": Hostile()}, "adept_return_binding_required", "resolve_identity"),
    ({"status": "ready", "acquisition_id": {"private": PRIVATE}},
     "adept_return_binding_required", "resolve_identity"),
    ({"status": "ready"}, "adept_return_binding_required", "resolve_identity"),
    ({"status": "no_acquisition"}, "provider_action_available", "provider_action_explicit"),
    ({"status": "adept_borrower_binding_required"},
     "adept_borrower_binding_required", "resolve_identity"),
])
def test_return_without_verified_acquisition_never_mutates_or_exposes_data(
    tmp_path, monkeypatch, origin, expected, retry,
):
    service, loan, paris = setup(tmp_path)
    try:
        monkeypatch.setattr(service, "adept_return_status_for_loan", lambda _: origin)
        monkeypatch.setattr(
            service, "return_adept_acquisition",
            lambda *_a: pytest.fail("No unverified ADEPT return"),
        )
        result = service.return_loan_prefer_adept(loan)
        assert result == {
            "status": expected,
            "scope": "adobe_first_paris",
            "retry": retry,
        }
        assert "NEVER_ECHO" not in json.dumps(result)
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()


@pytest.mark.parametrize(("raw_status", "expected"), [
    (PRIVATE, "adept_return_state_required"),
    (Hostile(), "adept_return_state_required"),
    ("adept_return_profile_conflict", "adept_return_profile_conflict"),
    ("ready", "adept_return_state_required"),
])
def test_existing_acquisition_with_untrusted_or_unready_source_stays_read_only(
    tmp_path, monkeypatch, raw_status, expected,
):
    service, loan, paris = setup(tmp_path)
    try:
        monkeypatch.setattr(service, "adept_return_status_for_loan", lambda _: {
            "status": raw_status, "acquisition_id": "adept-01",
            "return_available": False,
        })
        monkeypatch.setattr(
            service, "return_adept_acquisition",
            lambda *_a: pytest.fail("Unready source must not mutate Adobe"),
        )
        answer = service.return_loan_prefer_adept(loan)
        assert answer["status"] == expected
        assert answer["retry"] == "verification_only"
        assert "NEVER_ECHO" not in json.dumps(answer)
        assert service.storage.acquisitions(provider_id="paris")[0]["status"] == "complete"
        assert paris.posts == 0
    finally:
        service.close()


def test_unknown_attention_code_cannot_escape_identity_preflight(tmp_path, monkeypatch):
    service, loan, paris = setup(tmp_path)
    try:
        monkeypatch.setattr(
            service, "_return_loan_identity",
            lambda _: (_ for _ in ()).throw(
                AttentionRequired("private", code=PRIVATE),
            ),
        )
        result = service.return_loan_prefer_adept(loan)
        assert result["status"] == "failed"
        assert result["code"] == "loan_return_borrower_unproven"
        assert result["retry"] == "resolve_identity"
        assert "NEVER_ECHO" not in json.dumps(result)
        assert paris.posts == 0
    finally:
        service.close()


@pytest.mark.parametrize("code,expected", [
    (PRIVATE, "loan_return_borrower_unproven"),
    (True, "loan_return_borrower_unproven"),
    ({"signed": PRIVATE}, "loan_return_borrower_unproven"),
    ("loan_return_borrower_mismatch", "loan_return_borrower_mismatch"),
    ("loan_return_source_unavailable", "loan_return_source_unavailable"),
    ("loan_return_borrower_unproven", "loan_return_borrower_unproven"),
])
def test_read_only_loan_verification_has_same_closed_preflight_projection(
    tmp_path, monkeypatch, code, expected,
):
    service, loan, paris = setup(tmp_path)
    try:
        def refuse(_loan):
            raise AttentionRequired(PRIVATE, code=code)
        monkeypatch.setattr(service, "_return_loan_identity", refuse)
        outcome = service.verify_loan_return(loan)
        assert outcome == {
            "status": "failed", "scope": "adobe_first_paris", "code": expected,
        }
        assert "NEVER_ECHO" not in json.dumps(outcome)
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()


def test_read_only_verification_handles_raising_code_property_without_leaking(
    tmp_path, monkeypatch,
):
    class HostileAttention(AttentionRequired):
        def __getattribute__(self, name):
            if name == "code":
                raise RuntimeError(PRIVATE)
            return super().__getattribute__(name)

    service, loan, paris = setup(tmp_path)
    try:
        monkeypatch.setattr(
            service, "_return_loan_identity",
            lambda _loan: (_ for _ in ()).throw(
                HostileAttention(PRIVATE, code="loan_return_borrower_mismatch"),
            ),
        )
        outcome = service.verify_loan_return(loan)
        assert outcome == {
            "status": "failed", "scope": "adobe_first_paris",
            "code": "loan_return_borrower_unproven",
        }
        assert "NEVER_ECHO" not in json.dumps(outcome)
        assert paris.posts == 0 and paris.accounts_read == []
    finally:
        service.close()
