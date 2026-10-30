"""Provider -> Loan -> Library contracts for the two #3104 examples.

Synthetic HTML and IDs. Real provider/parser and read-only Core projection;
no GTK render, real account, cover GET, ACSM or database mutation.
"""
from dataclasses import asdict
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Loan
from cifereca.services import LibraryService
import test_http_provider as fixture


BOOKS = (
    ("future", "2050, une Histoire du Futur"),
    ("dragon", "La promesse du dragon"),
)


def activity(with_image):
    cards = []
    for key, title in BOOKS:
        image = f'<img src="/covers/{key}.jpg">' if with_image else ""
        cards.append(
            f'<div class="js-loan"><a href="/resources/edition-{key}">'
            f'{image}{title}</a><a href="/loans/loan-{key}">Fichier</a></div>'
        )
    return '<section class="active-loans">' + ''.join(cards) + '</section>'


def library_rows(loans, *, stored_covers=False):
    """Consume provider payloads through the existing Library facade."""
    entitlements = [dict(
        entitlement_id="right-" + loan.id, identity_id=loan.identity_id,
        provider_id=loan.source_provider, credential_id=loan.source_credential,
        account_id=loan.source_account_id, loan_id=loan.id,
        item_id=loan.record_id, entitlement_type="loan", state="active",
        metadata=({"cover_url": f"https://pret.bnf.fr/covers/{key}.jpg"}
                  if stored_covers else {}),
    ) for (key, _), loan in zip(BOOKS, loans)]
    host = NS(
        current_identity=NS(identity_id="hugues"),
        identities=[NS(identity_id="hugues")], last_loans=loans,
        storage=NS(
            artifacts=lambda: [], placements=lambda **kw: [],
            catalog_entries=lambda: [], entitlements=lambda: entitlements,
            acquisitions=lambda **kw: [],
        ),
    )
    return LibraryService.library_entries(host, identity_id="hugues")


@pytest.mark.parametrize("entrypoint", ["loans", "loan_inventory_snapshot"])
def test_both_observed_covers_survive_loan_serialization_and_library_projection(entrypoint):
    s, calls, _ = fixture.HTTPProviderTests().make(activity(True))
    try:
        account, = s.connect()
        account.identity_id = "hugues"
        result = getattr(s, entrypoint)(account)
        loans = list(result.loans) if entrypoint != "loans" else result
        # Cached model rehydration must preserve the provider's exact URL.
        reloaded = [Loan(**asdict(loan)) for loan in loans]
        rows = {row["item_id"]: row for row in library_rows(reloaded)}
        assert len(rows) == len(loans) == 2
        for (key, title), loan in zip(BOOKS, loans):
            expected = f"https://pret.bnf.fr/covers/{key}.jpg"
            assert loan.title == title
            assert loan.thumbnail_url == expected
            assert rows[loan.record_id]["thumbnail_url"] == expected
            assert rows[loan.record_id]["metadata"]["cover_resolution"]["source"] == "loan"
        assert not any(path.startswith(("/loans/", "/covers/")) for _, _, path in calls)
    finally:
        s.close()


def test_library_can_have_covers_while_observed_loans_have_none():
    s, calls, _ = fixture.HTTPProviderTests().make(activity(False))
    try:
        account, = s.connect()
        account.identity_id = "hugues"
        loans = s.loans(account)
        rows = library_rows(loans, stored_covers=True)
        assert len(rows) == len(loans) == 2
        assert all(loan.thumbnail_url is None for loan in loans)
        assert all(row["thumbnail_url"] for row in rows)
        assert all(row["metadata"]["cover_resolution"]["source"] == "entitlement"
                   for row in rows)
        assert not any(path.startswith(("/loans/", "/covers/")) for _, _, path in calls)
    finally:
        s.close()
