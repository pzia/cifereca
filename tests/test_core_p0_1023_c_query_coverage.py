"""#1023 C7: count the initial loaded C cohort, never a provider total."""
from contextlib import closing
from unittest.mock import Mock

from cifereca.core.notice_c_known_projection import (
    known_public_c_loaded_coverage, merge_public_notice_c_known_rows,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.gui.query_model import QueryState
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_search_results_view import _host


def test_known_count_distinguishes_loaded_page_and_current_filter(tmp_path):
    with closing(Storage(tmp_path / "coverage.sqlite")) as db:
        for idx in range(3):
            persist(db, verified_public(record_id=f"edition-{idx}", title="Notice prix"))
        page = search_public_notice_c(db, "prix", group_by_document=True, page_size=2, now=100)
        assert page.known_documents == 3 and page.has_more
        rows = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert known_public_c_loaded_coverage(rows, identity_id="alice") == (2, 3, "partial", True)
        assert known_public_c_loaded_coverage(rows, identity_id="bob") is None
        host = _host()
        host.search_page.count_label.set_tooltip_text = Mock()
        host._search_local_objects = {row["key"]: row for row in rows}
        host.search_controls.state = lambda: QueryState(text="prix")
        host._update_search_count(2)
        tooltip = host.search_page.count_label.set_tooltip_text.call_args.args[0]
        assert "2 notices chargées" in tooltip and "3 notices connues repérées" in tooltip
        assert "couverture incomplète" in tooltip
        host.search_controls.state = lambda: QueryState(text="absent")
        assert not host.search_controls.state().filter(rows)
        host._update_search_count(0)
        assert host.search_page.count_label.set_label.call_args.args[0] == "0 résultats · Connu"
        assert host.search_page.count_label.set_tooltip_text.call_args.args[0] == tooltip
        # A different query cohort cannot be combined into a fabricated count.
        second = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert known_public_c_loaded_coverage([rows[0], second[1]], identity_id="alice") is None
        # Budget exhaustion is an explicit lower bound, not a final zero.
        budget = search_public_notice_c(db, "prix", group_by_document=True, max_scan=1, now=100)
        assert budget.status == "budget_exceeded"
        limited = merge_public_notice_c_known_rows([], budget, identity_id="alice")
        if limited:
            host._search_local_objects = {row["key"]: row for row in limited}
            host._update_search_count(len(limited))
            assert "au moins" in host.search_page.count_label.set_tooltip_text.call_args.args[0]
        host._search_local_objects = {}
        host._update_search_count(0)
        assert "Lot C" not in host.search_page.count_label.set_tooltip_text.call_args.args[0]
        assert db.entitlements() == [] and db.acquisitions() == []
