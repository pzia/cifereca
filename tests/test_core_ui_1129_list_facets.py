"""List facets remain bounded, ordered and scoped to the owning controls."""
from types import SimpleNamespace as NS

from cifereca.gui.list_facet_controller import attach_row_facets


def test_facet_projection_keeps_order_overflow_and_correct_control_scope():
    events = []
    def facet(field, label, value):
        return NS(field=field, label=label, value=value)
    values = [
        facet("source", "Z", "paris"),
        facet("author", "B", "bob"),
        facet("author", "A", "alice"),
        facet("isbn", "ISBN", "ignored"),
        facet("language", "FR", "fr"),
    ]
    controls = object()
    window = NS(
        service=NS(provider_display_name=lambda key: key),
        _search_facet=lambda field, value, state: events.append((field, value, state)),
    )
    rows = [NS(key="a", author_actions=[1], source_action=object(), facet_actions=[1])]
    def project(facets):
        return facets[:2] + [facet("_overflow", "+2", "")]
    attach_row_facets(
        window, rows, {"a": {"key": "a"}}, controls,
        facets_for_book=lambda item, provider: values,
        project_list_facets=project,
    )
    assert [tag.field for tag, _ in rows[0].tags] == [
        "author", "author", "_overflow",
    ]
    assert rows[0].tags[2][1] is None
    rows[0].tags[0][1](None)
    assert events == [("author", "alice", controls)]
    assert rows[0].author_actions == []
    assert rows[0].source_action is None
    assert rows[0].facet_actions == []


def test_empty_facets_do_not_leave_stale_row_actions():
    row = NS(key="missing", tags=["stale"], author_actions=["old"],
             source_action="old", facet_actions=["old"])
    attach_row_facets(
        NS(service=NS(provider_display_name=str)), [row], {}, object(),
        facets_for_book=lambda item, provider: [],
        project_list_facets=lambda values: values,
    )
    assert row.tags == []
    assert row.author_actions == []
    assert row.source_action is None
    assert row.facet_actions == []