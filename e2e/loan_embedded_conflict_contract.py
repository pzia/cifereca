"""#4980: mapped native loan list/detail must obey current embedded ambiguity.

Uses the BnF probe's existing synthetic, already-admitted Core projection
boundary. This is not a new SQLite association or a supplier terrain claim.
"""
from copy import deepcopy

from cifereca.core.book_cover_source_projection import exact_loan_cover_scope
from cifereca.core.loan_source_identity import loan_source_key
from cifereca.gui.book_widgets import BookListCell, CoverView
from cifereca.gui.loans_controller import _library_loan_covers_updated


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def verify_loan_embedded_conflict(window, loan, *, await_condition, capture,
                                  report, image_gets, complete, checkpoint):
    checkpoint("embedded_conflict_setup")
    page = window.loan_page
    key = loan_source_key(loan)
    original = window._loan_cover_projection
    epoch, owner, rows = original
    first, = [row for row in rows if row.get("local") is True]
    embedded = first["thumbnail_url"]
    supplier = loan.thumbnail_url
    assert supplier and supplier != embedded
    assert window.loan_objects[key] is loan
    assert page.selection.get_selected_item().key == key
    before_gets = tuple(image_gets)
    scope = exact_loan_cover_scope(loan)
    cache = window._loan_cover_decision_cache
    revision = window._loan_cover_decision_revision
    assert cache.peek(scope, revision=revision).uri == embedded

    def visible_pair():
        left = [cell.cover for cell in walk(page.listview)
                if isinstance(cell, BookListCell) and cell.get_mapped()
                and cell._bound_key == key and cell.cover.get_mapped()]
        right = [cover for cover in walk(page.detail)
                 if isinstance(cover, CoverView) and cover.get_mapped()]
        return tuple(left + right) if len(left) == len(right) == 1 else ()

    def painted(address):
        covers = visible_pair()
        return len(covers) == 2 and all(
            cover._url == address and cover.picture.get_paintable() is not None
            and not cover.placeholder.get_visible() and not cover.spinner.get_visible()
            for cover in covers
        )

    other = deepcopy(first)
    other.update(artifact_id="synthetic-conflict-artifact",
                 placement_id="synthetic-conflict-placement",
                 thumbnail_url="file:///tmp/cifereca-fixture-unmaterialized-conflict.png")
    other["metadata"]["cover_resolution"]["url"] = other["thumbnail_url"]
    for proof in other["_exact_loan_cover_proofs"]:
        proof.update(artifact_id=other["artifact_id"], placement_id=other["placement_id"],
                     url=other["thumbnail_url"])
    # The second proof is admitted at the synthetic producer boundary, not
    # extracted or fetched. Neither candidate may be materialized on conflict.
    def begin_conflict():
        checkpoint("embedded_conflict_publish")
        _library_loan_covers_updated(window, [*rows, other])
        await_condition("embedded_conflict_placeholder", neutral, conflicted)

    def neutral():
        covers = visible_pair()
        current = page.selection.get_selected_item()
        return (current is not None and current.key == key
                and current.thumbnail_url is None and len(covers) == 2
                and all(cover._url is None
                        and cover.picture.get_paintable() is None
                        and cover.placeholder.get_visible()
                        and not cover.spinner.get_visible()
                        for cover in covers))

    def conflicted():
        checkpoint("embedded_conflict_validate")
        current_revision = window._loan_cover_decision_revision
        assert current_revision > revision
        decision = cache.peek(scope, revision=current_revision)
        assert decision.status == "ambiguous" and decision.tier == "embedded"
        assert decision.candidate_count == 2 and not decision.uri
        assert cache.peek(scope, revision=revision) is None
        for address in (embedded, supplier, other["thumbnail_url"]):
            assert cache.selected_token(scope, revision=current_revision, uri=address) is None
        assert tuple(image_gets) == before_gets
        assert window.loan_objects[key] is loan and loan.thumbnail_url == supplier
        assert window.service._account_context_epoch == epoch
        assert window.service.current_identity.identity_id == owner
        capture("10b-bnf-embedded-conflict-placeholder")
        # Restore the original exact proof through the same live callback.
        checkpoint("embedded_conflict_restore")
        _library_loan_covers_updated(window, list(rows))
        await_condition("embedded_conflict_recovered", lambda: painted(embedded), restored)

    def restored():
        checkpoint("embedded_conflict_validate_recovery")
        decision = cache.peek(scope, revision=window._loan_cover_decision_revision)
        assert decision.status == "selected" and decision.uri == embedded
        assert tuple(image_gets) == before_gets
        assert window.loan_objects[key] is loan and loan.thumbnail_url == supplier
        capture("10c-bnf-embedded-conflict-recovered")
        report["checks"]["P0_4980_embedded_ambiguous_placeholder_real_gtk"] = True
        complete()

    await_condition("embedded_conflict_initial_paint", lambda: painted(embedded), begin_conflict)
