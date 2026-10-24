"""Only card-scoped, positively observed BnF loan bibliography is projected.

No due date, cover, format or author is inferred from an unrelated book, a
public title match, a second session or a private HTTP capture.
"""
from bnf_token_fixture import connected


def page(*cards, outside=""):
    return (outside + '<section class="active-loans">'
            + ''.join(cards) + '</section>')


def card(loan, resource, *, inside=""):
    return (f'<div class="js-loan"><a href="/resources/{resource}">'
            f'Titre {resource}</a><a href="/loans/{loan}">Fichier</a>'
            + inside + '</div>')


def test_explicit_author_and_numeric_loan_label_only_if_evidenced():
    session, account, _loan, state, _, _ = connected()
    try:
        state["activity"] = page(card(
            "l1", "r1", inside='<span itemprop="author">Auteur attesté</span>',
        ))
        row, = session.loans(account)
        assert row.author == "Auteur attesté"
        assert row.document_type == "Prêt numérique"
        assert row.medium == "digital"
        assert row.due_at is None
        assert row.thumbnail_url is None
        assert row.media_format is None
        assert row.ebook_url is None
        assert session.last_loan_errors
    finally:
        session.close()


def test_two_loans_do_not_inherit_header_or_other_resource_author():
    session, account, _, state, _, _ = connected()
    try:
        state["activity"] = page(
            card("one", "edition-a", inside='<span itemprop="author">Autrice A</span>'),
            card("two", "edition-b"),
            outside='<header><span itemprop="author">Personne étrangère</span></header>',
        )
        records = {row.id: row for row in session.loans(account)}
        assert len(records) == 2
        assert records["one"].author == "Autrice A"
        assert records["two"].author is None
        assert all(row.document_type == "Prêt numérique" for row in records.values())
    finally:
        session.close()


def test_hidden_and_nested_foreign_book_authors_are_not_projected():
    session, account, _, state, _, _ = connected()
    try:
        state["activity"] = page(card("one", "edition-a", inside=(
            '<span hidden itemprop="author">Identifiant privé</span>'
            '<script><span itemprop="author">Ne pas utiliser</span></script>'
            '<article itemscope itemtype="https://schema.org/Book">'
            '<span itemprop="author">Un autre livre</span></article>'
        )))
        row, = session.loans(account)
        assert row.author is None
    finally:
        session.close()


def test_excessive_author_microdata_does_not_hide_existing_loan():
    session, account, _, state, _, _ = connected()
    try:
        state["activity"] = page(card(
            "one", "edition-a",
            inside=''.join(f'<span itemprop="author">Auteur {n}</span>'
                           for n in range(12)),
        ))
        row, = session.loans(account)
        assert row.author is None
        assert session._snapshot(state["activity"]).status == "partial"
    finally:
        session.close()


def test_another_isolated_session_cannot_supply_borrower_authors():
    first, a1, _, s1, _, _ = connected()
    second, a2, _, s2, _, _ = connected()
    try:
        s1["activity"] = page(card(
            "a", "book", inside='<span itemprop="author">Source A</span>',
        ))
        s2["activity"] = page(card(
            "a", "book", inside='<span itemprop="author">Source B</span>',
        ))
        first_rows = first.loans(a1)
        second_rows = second.loans(a2)
        assert first_rows[0].author == "Source A"
        assert second_rows[0].author == "Source B"
    finally:
        first.close()
        second.close()
