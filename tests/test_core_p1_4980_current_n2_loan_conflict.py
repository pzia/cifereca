"""#4980: current N2 embedded ambiguity must not become a legacy image."""
from copy import deepcopy
from types import SimpleNamespace as NS

from cifereca.core.book_cover_source_projection import exact_loan_cover_scope
from cifereca.gui.loan_cover_decision_bridge import current_loan_embedded_conflict
from cifereca.gui.loan_cover_n2_bridge import loan_embedded_cover_for_host
from cifereca.gui.borrower_public_c_bridge import show_public_loan_c
from tests.test_core_p1_4979_loan_gtk_projection import a_loan, a_row, ui_method


def test_current_n2_conflict_neutralizes_display_without_mutating_loan():
    adapt = ui_method()
    loan = a_loan(image="https://bnf.example/current.png")
    one = a_row(loan)
    other = deepcopy(one)
    other.update(artifact_id="artifact-2", placement_id="placement-2",
                 thumbnail_url="file:///home/alice/.cache/cifereca/other.png")
    other["metadata"]["cover_resolution"]["url"] = other["thumbnail_url"]
    other["_exact_loan_cover_proofs"][0].update(
        artifact_id=other["artifact_id"], placement_id=other["placement_id"],
        url=other["thumbnail_url"],
    )
    host = NS(
        service=NS(current_identity=NS(identity_id="alice"),
                   _account_context_epoch=7, _source_context_epoch=9),
        _loan_cover_projection=(7, "alice", (one,)),
        _loan_cover_decision_revision=1,
        _library_entries_cache=[one, other],
    )
    assert adapt(host, loan).thumbnail_url == one["thumbnail_url"]
    host._loan_cover_projection = (7, "alice", (one, other))
    host._loan_cover_decision_revision = 2
    for _ in range(2):  # list and detail consume the same current decision
        shown = adapt(host, loan)
        assert shown is not loan and shown.thumbnail_url is None
        assert shown.id == loan.id and shown.source_account_id == loan.source_account_id
        assert current_loan_embedded_conflict(host, loan)
        assert loan_embedded_cover_for_host(host, loan) is None
    scope = exact_loan_cover_scope(loan)
    cache = host._loan_cover_decision_cache
    assert cache.peek(scope, revision=2).status == "ambiguous"
    assert cache.peek(scope, revision=1) is None
    assert loan.thumbnail_url == "https://bnf.example/current.png"

    # Do not even invoke the weaker C presentation on a current conflict.
    assert not show_public_loan_c(
        host, loan, lambda: (_ for _ in ()).throw(AssertionError("fallback")),
    )
    # Recover only when a newer exact snapshot resolves the conflict.
    host._loan_cover_projection = (7, "alice", (one,))
    host._loan_cover_decision_revision = 3
    assert not current_loan_embedded_conflict(host, loan)
    assert adapt(host, loan).thumbnail_url == one["thumbnail_url"]
    # Historical compatibility is still explicit, not a new N2 assertion.
    host._loan_cover_projection = None
    assert not current_loan_embedded_conflict(host, loan)
    assert adapt(host, loan) is loan
