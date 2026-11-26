"""#4980: inspect actual mapped route paint leases at the native N4 boundary.

The six-view fixture has already exercised real list/detail N2/N3/N4 before
each capture. These synchronous context substitutions exercise its exact
bound guard, not a reconstructed predicate or an actual profile switch.
The initial audit is synchronous. A second pass intercepts the actual warm
N3 result queued by a fresh native route binding, delays its GTK sink, and
rejects it with a simulated A->B->A account epoch. The fixture context is
restored only after cancelling that request; a new native binding must
repaint. This is synthetic context substitution, not a live profile switch.
"""
from __future__ import annotations

from time import monotonic
from unittest.mock import patch

ROUTES = ("library", "recent", "search", "loans", "history", "recommendations")
STAGES = {f"00-notice-c-{route}-{variant}-offline": (route, variant)
          for route in ROUTES for variant in ("list", "detail")}
MARKER = "P0_4980_six_view_native_paint_lease_aba_audit"
LATE_MARKER = "P0_4980_six_view_native_late_n3_aba_rejected"


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
        self.rejected = set()
        self.rebound = set()

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

    def deliver_late(self, route, variant, n2, document, rebind, *,
                     later, guard, complete):
        """Hold one genuine CoverView N3 callback before its native N4 sink."""
        from cifereca.gui.book_widgets import CoverView

        stage = f"00-notice-c-{route}-{variant}-offline"
        assert stage in self.seen and stage not in self.rejected
        original_bind = CoverView.bind_document
        armed = []
        delivered = []
        service = self.window.service
        identity = service.current_identity
        owner = identity.identity_id
        account = service._account_context_epoch
        source = service._source_context_epoch
        foreign = "fixture-4980-foreign-profile"
        assert owner != foreign

        def bind(widget, actual_n2, actual_document, *, variant="list", **options):
            if (actual_n2 is not n2 or actual_document != document
                    or variant != STAGES[stage][1]):
                return original_bind(widget, actual_n2, actual_document,
                                     variant=variant, **options)
            assert not armed
            assert callable(options.get("is_current"))
            original_apply = widget._apply_result

            def receive(generation, loader, path):
                if generation != widget._generation:
                    return original_apply(generation, loader, path)
                widget._apply_result = original_apply
                assert not delivered
                delivered.append(True)

                def reject(deadline):
                    assert len(armed) == 1 and armed[0] is widget
                    if not widget.get_mapped():
                        assert monotonic() < deadline, "late N3 target was not mapped"
                        later(40, guard(lambda: reject(deadline)))
                        return
                    assert generation == widget._generation and loader is n2.loader
                    request = widget._request
                    check = widget._document_check
                    assert request.still_current and check() is True
                    assert path is not None and path.is_file()
                    assert service.current_identity is identity
                    assert (identity.identity_id, service._account_context_epoch,
                            service._source_context_epoch) == (owner, account, source)
                    before = loader.stats()
                    # Do not process any other GTK event with simulated
                    # context. B and the returning A' are distinct epochs.
                    with patch.object(identity, "identity_id", foreign), patch.object(
                            service, "_account_context_epoch", account + 1):
                        assert check() is False
                    with patch.object(service, "_account_context_epoch", account + 2):
                        assert identity.identity_id == owner and check() is False
                        original_apply(generation, loader, path)
                        assert widget.picture.get_paintable() is None
                        assert widget.placeholder.get_visible()
                        assert not widget.spinner.get_visible()
                        after = loader.stats()
                        assert after["ui_stale"] == before["ui_stale"] + 1
                        for key in ("ui_applied", "ui_failed", "ui_decode_count"):
                            assert after[key] == before[key]
                        # Cancel before restoring the fixture epochs. The
                        # rejected request must not become current again.
                        widget.reset()
                        assert not request.active
                    assert not request.still_current
                    self.rejected.add(stage)
                    later(40, guard(lambda: complete(request)))

                # The actual GLib idle delivery has reached the sink; delay
                # its dispatch once more to allow native mapping to settle.
                later(40, guard(lambda: reject(monotonic() + 8)))
                return False

            widget._apply_result = receive
            try:
                result = original_bind(widget, actual_n2, actual_document,
                                       variant=variant, **options)
            except BaseException:
                widget._apply_result = original_apply
                raise
            armed.append(widget)
            return result

        # The interception is limited to the synchronous native producer
        # rebind; no class method remains patched during asynchronous IO.
        with patch.object(CoverView, "bind_document", bind):
            rebind()
        assert len(armed) == 1

        def timeout():
            assert stage in self.rejected, "native late N3 callback timeout"

        later(12000, guard(timeout))

    def restored(self, route, variant, cover, revoked):
        stage = f"00-notice-c-{route}-{variant}-offline"
        assert stage in self.rejected and stage not in self.rebound
        assert cover._request is not revoked and not revoked.active
        assert cover._request.still_current and cover._document_check() is True
        self.rebound.add(stage)

    def complete(self):
        # Native GTK synthetic evidence remains distinct from a real
        # profile switch, independent visual review and supplier terrain.
        assert self.seen == self.rejected == self.rebound == set(STAGES)
        self.report["checks"][MARKER] = True
        self.report["checks"][LATE_MARKER] = True
        self.report["notice_c_native_lease_audit_count"] = len(self.seen)
        self.report["notice_c_native_late_aba_count"] = len(self.rebound)
