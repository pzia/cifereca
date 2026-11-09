"""#5209: an observed PNB .do link cannot be reused by another identity.

The test uses synthetic DOM/signed URLs; no supplier requests or credentials.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_biblioaccess_sso import _synced_session, mod

_PNB = (
    "https://pnb-dilicom.centprod.com/ab//cdef/1234567890123/abcd/"
    "abcdefghijklmnopqrstuvwxy/"
    "0000000000000-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.do"
)


def _row():
    return SimpleNamespace(
        product_ref="10001", book_id="20001",
        title="SYNTHETIC-BOOK", due_at_local="30/11/2026 12:00:00",
        acsm_offer_present=True, acsm_url=_PNB, delivery_kind="pnb_do",
    )


def test_pnb_delivery_refuses_foreign_owner_or_credential_before_live_get(
    tmp_path, monkeypatch,
):
    from cifereca.plugin_api import read_loan_inventory
    import biblioaccess_acsm_delivery

    one, first_http, first_calls = _synced_session(
        rows=[_row()], credential="credential-A",
    )
    two, second_http, second_calls = _synced_session(
        rows=[_row()], credential="credential-B",
    )
    staged = []
    def stage(url, directory):
        staged.append(url)
        path = directory / "synthetic.acsm"
        path.write_bytes(b"synthetic")
        return path
    monkeypatch.setattr(biblioaccess_acsm_delivery, "stage_loan_acsm", stage)
    try:
        account_a, = one.connect()
        account_b, = two.connect()
        account_a.identity_id = "identity-A"
        account_b.identity_id = "identity-B"
        loan_a, = read_loan_inventory(one, account_a).loans
        loan_b, = read_loan_inventory(two, account_b).loans
        assert loan_a.identity_id == "identity-A"
        assert loan_b.identity_id == "identity-B"
        before = first_calls.count("live_loans")
        for wrong in (
            loan_b,
            replace(loan_a, identity_id="identity-B"),
            replace(loan_a, source_credential="credential-B"),
            replace(loan_a, source_account_id="another-account"),
        ):
            with pytest.raises(
                mod.ProviderCapabilityError, match="loan_account_unbound",
            ):
                one.download_digital(wrong, account_a, tmp_path)
        assert first_calls.count("live_loans") == before
        assert staged == []
        assert not list(tmp_path.iterdir())

        # A correctly scoped offer is still available to the original owner.
        result, url = one.download_digital(loan_a, account_a, tmp_path)
        assert result.name == "synthetic.acsm"
        assert url == "" and staged == [_PNB]
        assert first_calls.count("live_loans") == before + 1
        assert second_calls.count("live_loans") == 1
    finally:
        one.close()
        two.close()
