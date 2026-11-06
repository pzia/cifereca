"""#4761: common Search format filter is local, bounded and evidence-based.

Synthetic catalogue candidates; no provider GET, mutation or generation.
"""
from types import SimpleNamespace as NS

from recommendation_engine import RankedCandidate
from recommendations import (
    UxRecommendationsView, _candidate_formats,
)


def ranked(fmt=None, *, media_format=None):
    return RankedCandidate(
        item=NS(title="Livre test", author="Auteur", format=fmt,
                media_format=media_format),
        score=80, sources=("catalogue",), reasons=(),
    )


def view_with_formats(selected):
    view = object.__new__(UxRecommendationsView)
    view._score = NS(get_value=lambda: 0)
    view._query_controls = NS(
        filter_text=lambda: "", selected_sources=lambda: (),
        selected_languages=lambda: (), selected_formats=lambda: selected,
    )
    return view


def test_exact_local_format_filter_and_all_reset():
    epub = ranked("EPUB")
    pdf = ranked("pdf")
    unknown = ranked()
    view = view_with_formats(("epub",))
    assert view._matches(epub)
    assert not view._matches(pdf)
    assert not view._matches(unknown)
    view._query_controls.selected_formats = lambda: ("epub", "pdf")
    assert view._matches(epub) and view._matches(pdf)
    assert not view._matches(unknown)
    view._query_controls.selected_formats = lambda: ()
    assert all(view._matches(item) for item in (epub, pdf, unknown))


def test_only_explicit_exact_media_formats_qualify_as_facets():
    assert _candidate_formats(ranked("EPUB").item) == ("epub",)
    assert _candidate_formats(ranked(" epub ", media_format="PDF").item) == (
        "epub", "pdf",
    )
    assert _candidate_formats(ranked("epub/pdf").item) == ()
    assert _candidate_formats(ranked("application/epub+zip").item) == ()
    assert _candidate_formats(ranked(42).item) == ()

    class BadFormat:
        @property
        def format(self):
            raise RuntimeError("untrusted provider accessor")
        media_format = None
    assert _candidate_formats(BadFormat()) == ()


def test_local_facets_published_without_new_search_and_old_host_supported():
    view = view_with_formats(("pdf",))
    calls = []
    view._query_controls.set_choices = lambda **kw: calls.append(kw)
    view._reload_languages([ranked("EPUB"), ranked("epub"), ranked("PDF"), ranked()])
    assert calls == [{
        "languages": (),
        "formats": (("epub", "EPUB"), ("pdf", "PDF")),
    }]

    # Pre-#4761 Core has only source/language facets. The plugin remains
    # usable without exposing a fake format selection or performing I/O.
    legacy = NS(
        filter_text=lambda: "", selected_sources=lambda: (),
        selected_languages=lambda: (),
        set_choices=lambda **kw: calls.append(kw),
    )
    view._query_controls = legacy
    view._reload_languages([ranked("EPUB")])
    assert calls[-1] == {"languages": ()}
    assert view._matches(ranked("PDF"))
