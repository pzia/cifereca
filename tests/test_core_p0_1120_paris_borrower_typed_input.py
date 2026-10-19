"""#1120: unproven provider coordinates never select a Paris ADEPT profile."""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


PRIVATE = "https://private.invalid/loan.acsm?token=PRIVATE_BORROWER"


class HostileValue:
    def __bool__(self):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)


class HostileString(str):
    def strip(self, *_args):
        raise AssertionError(PRIVATE)

    def __str__(self):
        raise AssertionError(PRIVATE)


@pytest.mark.parametrize("field", ["source_account_id", "source_credential"])
@pytest.mark.parametrize("bad", [
    None, HostileValue(), HostileString("linked-123"), {"account": PRIVATE},
    1234, True,
])
def test_non_native_borrower_coordinates_fail_closed_before_binding_lookup(
    tmp_path, monkeypatch, field, bad,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        lookups = []
        def forbidden_lookup(**_kwargs):
            lookups.append(True)
            raise AssertionError("no grant read for malformed provenance")
        monkeypatch.setattr(
            service.storage, "paris_borrower_adept_binding_get",
            forbidden_lookup,
        )
        malformed = replace(loan, **{field: bad})
        assert service._paris_adept_identity_for_loan(
            malformed, connection_identity_id="alice",
        ) == (None, "adept_borrower_ambiguous")
        assert not lookups
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


def test_native_borrower_coordinates_keep_historical_whitespace_semantics(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        padded = replace(
            loan, source_credential=" alice-paris ",
            source_account_id=" linked-123 ",
        )
        assert service._paris_adept_identity_for_loan(
            padded, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
    finally:
        service.close()
