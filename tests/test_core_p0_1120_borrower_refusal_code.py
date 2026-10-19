"""#1120: only exact Paris borrower-refusal codes cross acquisition boundaries."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.adept_borrower import public_borrower_refusal_code
from cifereca.core.loan_acquisition_admission import resolve
from tests.test_automatic_loan_admission import _forbid_work, _setup
from tests.test_core_p0_1120_adept_profile_guard_extraction import _fixture


PRIVATE = "https://private.invalid/borrow.acsm?token=NEVER_EXPOSE"


class MalformedCode:
    def __bool__(self):
        raise AssertionError(PRIVATE)

    def __eq__(self, _other):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)


class StrSubclass(str):
    def __eq__(self, _other):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)


INVALID = [PRIVATE, "", False, 0, {}, MalformedCode(), StrSubclass("adept_borrower_binding_required")]
VALID = [
    "adept_borrower_ambiguous", "adept_borrower_binding_required",
    "adept_borrower_connection_changed", "adept_borrower_profile_changed",
]


@pytest.mark.parametrize("code", INVALID)
def test_pre_admission_refusal_blocks_private_and_falsey_codes_without_work(
    tmp_path, monkeypatch, code,
):
    service, loan = _setup(tmp_path)
    try:
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            lambda *_a, **_kw: (None, code),
        )
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=NS(
            warning=lambda *_a, **_kw: None, info=lambda *_a, **_kw: None,
        ))
        assert result.refusal == {
            "status": "attention_required", "loan_id": loan.id,
            "code": "adept_borrower_ambiguous", "retry": "explicit_only",
        }
        assert result.identity is None and result.entitlement_id is None
        assert PRIVATE not in repr(result.refusal)
    finally:
        service.close()


@pytest.mark.parametrize("code", INVALID)
def test_pre_adept_refusal_persists_only_closed_code_without_profile_attempt(code):
    invoke, store, _borrower, resolver, profiles, loan, _opts = _fixture(
        borrower_code=code,
    )
    result = invoke()
    assert result == {
        "status": "attention_required", "loan_id": loan.id,
        "code": "adept_borrower_ambiguous", "retry": "explicit_only",
    }
    store.assert_called_once()
    assert store.call_args.args[0]["attention_code"] == "adept_borrower_ambiguous"
    resolver.assert_not_called()
    profiles.assert_not_called()
    assert PRIVATE not in repr(result) + repr(store.call_args.args[0])


@pytest.mark.parametrize("code", VALID)
def test_historical_borrower_refusals_keep_their_exact_machine_codes(code):
    assert public_borrower_refusal_code(code) == code
    invoke, store, _, resolver, profiles, loan, _opts = _fixture(
        borrower_code=code,
    )
    result = invoke()
    assert result["code"] == code
    store.assert_called_once()
    assert store.call_args.args[0]["attention_code"] == code
    resolver.assert_not_called()
    profiles.assert_not_called()
