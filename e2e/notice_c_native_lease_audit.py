"""#4980: inspect actual mapped route paint leases at the native N4 boundary.

The six-view fixture has already exercised real list/detail N2/N3/N4 before
each capture. These synchronous context substitutions exercise its exact
bound guard, not a reconstructed predicate or an actual profile switch.
No GTK event is dispatched with substituted context. The actual late N3
callback case remains in notice_c_recommendation_contract; this audit alone
does not certify an integrated asynchronous six-view ABA scenario.
"""
from __future__ import annotations

from unittest.mock import patch

ROUTES = ("library", "recent", "search", "loans", "history", "recommendations")
STAGES = {f"00-notice-c-{route}-{variant}-offline": (route, variant)
          for route in ROUTES for variant in ("list", "detail")}
MARKER = "P0_4980_six_view_native_paint_lease_aba_audit"


def audit_guard(service, check):
    """Synchronous negative probes against a guard captured by a real widget."""
    identity = service.current_identity
    owner = identity.identity_id
    account_epoch = service._account_context_epoch
    source_epoch = service._source_context_epoch
    assert type(owner) is str and owner
    assert type(account_epoch) is int and type(source_epoch) is int
    assert callable(check) and check() is True
    foreign = "fixture-4980-foreign-profile"
    assert owner != foreign
    # Isolate the account epoch from source changes: A -> B -> A must not
    # be accepted merely because the public document and N3 bytes still match.
    with patch.object(identity, "identity_id", foreign), patch.object(
            service, "_account_context_epoch", account_epoch + 1):
        assert check() is False
    with patch.object(service, "_account_context_epoch", account_epoch + 2):
        assert identity.identity_id == owner and check() is False
    # Source-context invalidation is independently required at the N4 sink.
    with patch.object(service, "_source_context_epoch", source_epoch + 1):
        assert check() is False
    # The original fixture is unmodified; no callback or profile transition
    # has run while the values were patched, and no stale lease was repainted.
    assert service.current_identity is identity
    assert (identity.identity_id, service._account_context_epoch,
            service._source_context_epoch) == (owner, account_epoch, source_epoch)
    assert check() is True


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def mapped_cover(window, route, variant):
    from gi.repository import Gdk
    from cifereca.gui.book_widgets import BookListCell, CoverView
    from cifereca.gui.detail_panel import DetailPanel

    expected = ("notice-c-recommendations-fixture" if route == "recommendations"
                else route)
    assert window.view_stack.get_visible_child_name() == expected
    page = window.view_stack.get_visible_child()
    assert page is not None and page.get_mapped()
    if variant == "list":
        candidates = [cell.cover for cell in walk(page)
                      if isinstance(cell, BookListCell) and cell.get_mapped()
                      and getattr(getattr(cell, "_thumbnail_item", None),
                                  "_cover_document_ref", None) is not None]
    else:
        panels = [panel for panel in walk(page)
                  if isinstance(panel, DetailPanel) and panel.get_mapped()]
        assert len(panels) == 1
        candidates = [cover for cover in walk(panels[0])
                      if isinstance(cover, CoverView) and cover.get_mapped()]
    assert len(candidates) == 1
    cover = candidates[0]
    assert cover.get_mapped() and isinstance(cover.picture.get_paintable(), Gdk.Texture)
    assert not cover.placeholder.get_visible() and not cover.spinner.get_visible()
    assert cover._loader is window.covers and cover._request.still_current
    return cover


class NativeLeaseAudit:
    def __init__(self, window, capture, report):
        self.window, self.capture_original, self.report = window, capture, report
        self.seen = set()

    def capture(self, stage):
        route_variant = STAGES.get(stage)
        if route_variant is not None:
            assert stage not in self.seen
            cover = mapped_cover(self.window, *route_variant)
            check, request, generation = (cover._document_check, cover._request,
                                           cover._generation)
            texture = cover.picture.get_paintable()
            loader = cover._loader
            before = loader.stats()
            audit_guard(self.window.service, check)
            after = loader.stats()
            for key in ("download_ok", "download_failed", "ui_applied", "ui_failed",
                        "ui_stale", "preview_created"):
                assert after[key] == before[key]
            assert cover._document_check is check and cover._request is request
            assert cover._generation == generation and request.still_current
            assert cover.picture.get_paintable() is texture
            assert not cover.placeholder.get_visible()
            self.seen.add(stage)
        self.capture_original(stage)

    def complete(self):
        # Keep this receipt distinct from the actual delayed callback and
        # from independent visual/terrain review; only closed stage count.
        assert self.seen == set(STAGES)
        self.report["checks"][MARKER] = True
        self.report["notice_c_native_lease_audit_count"] = len(self.seen)
