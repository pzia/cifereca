"""GTK-independent projection of row facet tags (#1129)."""
from __future__ import annotations


def attach_row_facets(window, rows, objects, controls, *, facets_for_book, project_list_facets):
    allowed = {'author': 0, 'format': 1, 'language': 2, 'collection': 3, 'source': 4}
    for row in rows:
        facets=[
            facet for facet in facets_for_book(
                objects.get(row.key,{}),
                window.service.provider_display_name,
            )
            if facet.field in allowed
        ]
        facets.sort(key=lambda facet:(allowed[facet.field], facet.label.casefold()))
        facets=project_list_facets(facets)
        row.tags=[
            (
                facet,
                None if facet.field=='_overflow' else
                (lambda _b,f=facet.field,text=facet.value:window._search_facet(f,text,controls)),
            )
            for facet in facets
        ]
        row.author_actions=[];row.source_action=None;row.facet_actions=[]

