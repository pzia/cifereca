"""P0 #5975: an automatic loan can link only one native exact right.

The provider-wide entitlement inventory is not authorization. The loan owner,
edition, borrower and existing right ID must match without object coercion.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_acquisition_admission import resolve
from tests.test_automatic_loan_admission import LOGGER, _forbid_work, _setup


class Hostile:
    def __eq__(self, _other):
        raise AssertionError("never compare a foreign entitlement field")

    def __str__(self):
        raise AssertionError("never stringify a foreign entitlement field")

    def __bool__(self):
        raise AssertionError("never test foreign entitlement truthiness")


class HostileString(str):
    def __eq__(self, _other):
        raise AssertionError("never compare a str subclass")


class HostileDict(dict):
    def get(self, *_args, **_kwargs):
        raise AssertionError("never invoke an untrusted mapping's get")


def _right(**changes):
    values = {
        "entitlement_id": "right-a",
        "loan_id": "loan-0",
        "identity_id": "default",
        "provider_id": "paris",
        "credential_id": "source-a",
        "account_id": "primary",
        "item_id": "edition-0",
        "entitlement_type": "loan",
        "state": "active",
    }
    values.update(changes)
    return values


def _check(tmp_path, monkeypatch, rows):
    service, loan = _setup(tmp_path)
    try:
        _forbid_work(service, monkeypatch)
        monkeypatch.setattr(
            service, "_paris_adept_identity_for_loan",
            # The entitlement selector must run under a positive borrower
            # grant; (None, None) is deliberately denied before rights.
            lambda *_args, **_kwargs: ("default", None),
        )
        # Cross-identity veto is independently tested; exercise only the
        # final entitlement selector with exact read-only inventory rows.
        monkeypatch.setattr(
            service, "_cross_identity_loan_conflict",
            lambda *_args, **_kwargs: False,
        )
        monkeypatch.setattr(
            service.storage, "entitlements", lambda **_kw: rows,
        )
        monkeypatch.setattr(
            service.storage, "acquisitions", lambda **_kw: [],
        )
        result = resolve(service, loan, _TRACE_LOG=LOGGER)
        assert result.refusal is None
        assert result.identity.identity_id == "default"
        return result.entitlement_id
    finally:
        service.close()


def test_single_exact_right_preserves_existing_binding(tmp_path, monkeypatch):
    assert _check(tmp_path, monkeypatch, [_right()]) == "right-a"


@pytest.mark.parametrize("bad", [
    None, "", False, 0, [], {}, Hostile(),
    HostileString("right-a"),
])
def test_missing_or_foreign_right_id_never_links(tmp_path, monkeypatch, bad):
    assert _check(
        tmp_path, monkeypatch, [_right(entitlement_id=bad)],
    ) is None


@pytest.mark.parametrize("field", [
    "loan_id", "identity_id", "provider_id", "credential_id",
    "account_id", "item_id", "entitlement_type",
])
@pytest.mark.parametrize("bad", [Hostile(), HostileString("loan-0"), False])
def test_hostile_right_coordinate_never_compares_or_links(
    tmp_path, monkeypatch, field, bad,
):
    assert _check(
        tmp_path, monkeypatch, [_right(**{field: bad})],
    ) is None


def test_foreign_dict_subclass_is_not_an_attested_entitlement(
    tmp_path, monkeypatch,
):
    assert _check(tmp_path, monkeypatch, [HostileDict(_right())]) is None


def test_corrupt_duplicate_does_not_make_one_right_look_unique(
    tmp_path, monkeypatch,
):
    assert _check(
        tmp_path, monkeypatch,
        [_right(), _right(entitlement_id=Hostile())],
    ) is None


def test_two_distinct_exact_rights_are_ambiguous(tmp_path, monkeypatch):
    assert _check(
        tmp_path, monkeypatch,
        [_right(), _right(entitlement_id="right-b")],
    ) is None


def test_foreign_borrower_edition_and_provider_not_counted_as_exact_duplicates(
    tmp_path, monkeypatch,
):
    assert _check(
        tmp_path, monkeypatch, [
            _right(),
            _right(account_id="someone-else", entitlement_id="foreign-borrower"),
            _right(item_id="other-edition", entitlement_id="foreign-edition"),
            _right(provider_id="other", entitlement_id="foreign-provider"),
        ],
    ) == "right-a"
