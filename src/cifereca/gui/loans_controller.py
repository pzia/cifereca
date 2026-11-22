"""GTK loans page/controller. Host owns state; Core owns all DRM decisions (#1131).

The functions accept the existing MainWindow host so callbacks keep their task
runner, current-identity and UI-thread semantics. Do not import MainWindow here.
"""
from __future__ import annotations

from typing import Any
from copy import copy

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from ..models import Loan
from ..core.loan_source_identity import loan_source_key, unambiguous_loan_rows
from ..core.loan_live_projection import current_actionable_loan_rows
from ..core.covers import candidate
from ..core.loan_cover_fallback import loan_local_cover, materialized_loan_cover_rows
from .book_widgets import BookListItem
from .loan_view import LOAN_FILTER_LABELS, loan_context_label, loan_due_presentation, loan_medium_counts, loan_row_metadata
from .master_detail import MasterDetailPage
from .query_controls import MultiSelect
from .task_runner import TaskPriority
from .loan_renewal_feedback import loan_renewal_feedback
from .detail_model import fr_date
from . import history_language_projection


def _build_loans_page(self) -> MasterDetailPage:
    page = MasterDetailPage(self, "Mes emprunts", "Prêts physiques et numériques", "Aucun emprunt affiché", "Vérifiez l’identité et les filtres, puis actualisez les emprunts. Une liste vide ne prouve pas l’absence de prêts chez le fournisseur.")
    history = Gtk.Button(label="Historique"); history.connect("clicked", lambda _b: self.navigate("history")); page.header_actions.append(history)
    # Open on every loan medium: the digital-only default hid physical loans.
    self.loan_filter = "all"
    segment = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    buttons: list[Gtk.ToggleButton] = []
    self.loan_filter_buttons: dict[str, Gtk.ToggleButton] = {}
    for value, label in [("all", "Tous"), ("physical", "Physiques"), ("digital", "Numériques")]:
        b = Gtk.ToggleButton(label=label); b.set_name(value)
        if buttons: b.set_group(buttons[0])
        if value == "all": b.set_active(True)
        b.connect("toggled", self._loan_filter_toggled)
        segment.append(b); buttons.append(b)
        self.loan_filter_buttons[value] = b
    page.toolbar.append(segment)
    self.loan_scope_count = Gtk.Label(label="", xalign=0)
    self.loan_scope_count.add_css_class("caption")
    self.loan_scope_count.add_css_class("dim-label")
    self.loan_scope_count.set_tooltip_text(
        "Nombre affiché dans le filtre actif, comparé au volume total des prêts"
    )
    page.toolbar.append(self.loan_scope_count)
    from cifereca.language import language_label
    self.loan_languages = MultiSelect('Langues', [(code, language_label(code)) for code in ('fr','en','de','es','it','pt','nl')], self._render_loans)
    page.toolbar.append(self.loan_languages)
    refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic"); refresh.set_tooltip_text("Actualiser les emprunts")
    refresh.set_tooltip_text("Actualiser les inventaires des prêts pour l’identité active, sans emprunt ni retour")
    refresh.connect("clicked", lambda _b: self.refresh_loans(True, refresh)); page.header_actions.append(refresh)
    page.connect_selected(self._loan_selected)
    return page


def _loan_filter_toggled(self, button: Gtk.ToggleButton) -> None:
    if button.get_active():
        self.loan_filter = button.get_name() or "all"
        self._render_loans()


def _populate_loans(self, rows: list[Loan]) -> None:
    # Defense in depth: a late global opening-sync callback or cross-profile
    # service snapshot must not be rendered under the selected identity.
    identity_id = getattr(getattr(self.service, "current_identity", None), "identity_id", None)
    rows = [row for row in rows if type(row) is Loan
            and type(identity_id) is str and bool(identity_id)
            and type(row.identity_id) is str
            and row.identity_id == identity_id]
    # A duplicated exact SourceKey cannot render two actionable rows backed
    # by a dict holding only the last card. Identical replays display once;
    # conflicting cards are not promoted into the click/renew/download model.
    rows = list(unambiguous_loan_rows(rows))
    self.service.last_loans = rows
    # A fresh provider inventory changes the evidence epoch even when the
    # Library snapshot is unchanged. Cached thumbnails from the prior Loan
    # DTO must not override a newer supplier image or disappearance.
    self._loan_cover_decision_revision = (
        getattr(self, "_loan_cover_decision_revision", 0) + 1
    )
    # The same upstream loan can be visible under two Paris credentials.
    # Preserve both source-bound rows instead of overwriting by loan.id.
    self.loan_objects = {loan_source_key(x): x for x in rows}
    self._render_loans()
    self._render_library()
    if hasattr(self,'_refresh_statistics'):self._refresh_statistics()


def _library_loan_covers_updated(self, entries: list[dict[str, Any]]) -> None:
    """Repaint loans from a newly committed, identity-scoped Library snapshot.

    Loan and Library refreshes complete independently. A late local Placement
    can appear after the loan list was first painted, while a subsequent click
    sees its cover in the detail. Use only the already delivered Core rows;
    never fetch a provider, scan a file or guess by bibliographic title.
    """
    if (getattr(self, "_closed", False)
            or getattr(self, "_library_all_profiles", False) is True
            or not hasattr(self, "loan_page")):
        return
    identity = getattr(getattr(self, "service", None), "current_identity", None)
    identity_id = getattr(identity, "identity_id", None)
    if type(identity_id) is not str or not identity_id:
        return
    rows = materialized_loan_cover_rows(entries)
    if entries and not rows:
        return  # Incomplete/oversize view is not an authoritative snapshot.
    epoch = getattr(self.service, "_account_context_epoch", None)
    self._loan_cover_projection = (epoch, identity_id, rows)
    # A loan refresh already running may finish with an older library read.
    # Its loan DTOs are still applicable, but must not revoke this newer
    # successful Library projection merely by completing last.
    self._loan_cover_library_revision = (
        getattr(self, "_loan_cover_library_revision", 0) + 1
    )
    # Both independent proof producers advance one monotonic cover revision.
    self._loan_cover_decision_revision = (
        getattr(self, "_loan_cover_decision_revision", 0) + 1
    )
    # This revision was produced by a later, identity/epoch-scoped Library
    # cover snapshot. Unlike a loan inventory refresh it may change ONLY
    # image presentation; retain the bound GTK list row if that is all.
    self._loan_cover_in_place_revision = self._loan_cover_decision_revision
    self._render_loans()


def _loan_display_cover(self, loan: Loan) -> Loan:
    """Display-only image fallback; retain the original loan for every action.

    The library's already materialized read-only snapshot is reused. If the
    exact loan + Placement proof is absent, ambiguous, stale or from another
    profile, keep the supplier's thumbnail unchanged (including None).
    """
    # Trust only a URI admitted by the shared Core cover resolver.
    # The supplier's original Loan remains untouched for all actions.
    original_url = loan.thumbnail_url
    safe_source = (
        type(original_url) is str and
        original_url.lower().startswith(("https://", "http://")) and
        candidate(original_url, tier="catalog", source="loan-provider") is not None
    )
    # Keep a validated supplier thumbnail as the safe fallback, but do not
    # short-circuit a more authoritative, loan-scoped embedded/origin proof.
    # A provider thumbnail is catalog-tier evidence, not proof that the
    # corresponding local EPUB/Placement cover is absent.
    safe_preview = loan if safe_source or not original_url else copy(loan)
    if not safe_source and safe_preview is not loan:
        safe_preview.thumbnail_url = None
    active = getattr(getattr(self.service, "current_identity", None), "identity_id", None)
    if active is None or loan.identity_id != active:
        return safe_preview
    evidence = getattr(self, "_loan_cover_projection", None)
    trusted_snapshot = (
        type(evidence) is tuple and len(evidence) == 3
        and evidence[0] == getattr(self.service, "_account_context_epoch", None)
        and evidence[1] == active and type(evidence[2]) is tuple
        and bool(evidence[2])
    )
    cover_rows = (
        evidence[2] if trusted_snapshot
        else getattr(self, "_library_entries_cache", ())
    )
    # Same N1 decision across list and detail: no second borrower/Placement
    # scan after the first scoped revision is already in the N2 cache.
    # The compatibility fallback below remains for headless/legacy windows,
    # empty or incomplete Library snapshots and no-provenance loans.
    decision = None
    if trusted_snapshot:
        from cifereca.gui.loan_cover_decision_bridge import loan_cover_decision
        decision = loan_cover_decision(self, loan, cover_rows)
    resolved = (
        decision if decision is not None
        else loan_local_cover(loan, cover_rows)
    )
    selected_url = (
        resolved.uri if decision is not None else resolved.url
    ) if resolved.status == "selected" else ""
    if not selected_url:
        return safe_preview
    # A distinct catalog cover never replaces an already validated exact
    # supplier thumbnail. Only a verified stronger tier may supersede it.
    # Ambiguous/stale/foreign proofs leave the source thumbnail untouched.
    if safe_source and resolved.tier not in ("embedded", "origin"):
        return loan
    if safe_source and selected_url == original_url:
        return loan
    displayed = copy(safe_preview)
    displayed.thumbnail_url = selected_url
    return displayed


def _render_loans(self) -> None:
    if not hasattr(self, "loan_page"):
        return
    # Service cache/async writers can be updated independently of GTK; never
    # trust a mixed-identity last_loans projection at display time.
    identity_id = getattr(getattr(self.service, "current_identity", None), "identity_id", None)
    # The service cache is independently mutable; only the active GTK
    # action map can authorize a loan row and its documentary N2 evidence.
    # In particular, reject a replayed/equal DTO that is not the same object.
    # Keep the dependency local: headless controller contract harnesses
    # execute this method independently of module-level imports.
    from cifereca.core.loan_live_projection import current_actionable_loan_rows
    all_loans = current_actionable_loan_rows(
        self.service.last_loans, getattr(self, "loan_objects", {}),
        identity_id=identity_id,
    )
    counts = loan_medium_counts(all_loans)
    if hasattr(self, "loan_filter_buttons"):
        for key, button in self.loan_filter_buttons.items():
            label = LOAN_FILTER_LABELS.get(key, key)
            button.set_label(f"{label} · {counts.get(key, 0)}")
    scope_context = (
        identity_id,
        getattr(self.service, "_account_context_epoch", None),
        getattr(self.service, "_source_context_epoch", None),
    )
    ranked_rows: list[tuple[tuple[int, int], BookListItem]] = []
    wanted_languages = set(self.loan_languages.values()) if hasattr(self, 'loan_languages') else set()
    display_loans = []
    for loan in all_loans:
        if self.loan_filter != "all" and loan.medium != self.loan_filter:
            continue
        display_loans.append(loan)
    if wanted_languages and display_loans:
        # The old per-loan _known_item_languages lookup read the entire
        # SQLite catalogue on GTK even without a language filter. Reuse one
        # fresh, local snapshot only for an actual filtered render.
        display_loans = history_language_projection.loan_rows_with_languages(
            display_loans, wanted_languages,
            self.service.storage.catalog_entries(),
        )
    from cifereca.gui.loan_cover_n2_bridge import loan_embedded_cover_for_host
    for loan in display_loans:
        due = loan_due_presentation(loan.due_at, is_late=bool(loan.is_late))
        subtitle = loan.library or ("Prêt numérique" if loan.medium == "digital" else "Prêt physique")
        # BnF (and other providers) can supply a verified author on the
        # exact loan. Display that author without borrowing names from a
        # homonymous catalogue record or another loan's thumbnail.
        if type(loan.author) is str and loan.author.strip():
            subtitle = loan.author.strip() + " · " + subtitle
        meta = loan_row_metadata(loan.due_at, due, document_type=loan.document_type, provider=loan.provider)
        display = _loan_display_cover(self, loan)
        source_key = loan_source_key(loan)
        item = BookListItem(
            source_key, loan.title, subtitle, meta, due.label,
            display.thumbnail_url, badge_style=due.style,
        )
        evidence = loan_embedded_cover_for_host(self, loan)
        if evidence is not None:
            # A loan row is not a Placement identity. The Core link above is
            # the permission to display this exact owner-specific source.
            item._cover_document_ref = evidence.scope
            item._cover_local_evidence = evidence
        else:
            # The Loan DTO itself may attest its direct supplier image. A
            # stronger origin/EPUB from Library never gets downgraded to it.
            from cifereca.gui.loan_supplier_document_cover_bridge import exact_supplier_loan_cover
            supplier = exact_supplier_loan_cover(self, loan, displayed=display)
            if supplier is not None:
                item._cover_document_ref = supplier.document
                item._cover_loan_supplier_evidence = supplier
                item._loan_source_item = loan
            elif display.thumbnail_url is None:
                # Canonical C never creates a loan/borrower entitlement.
                # Exact provider-issued book record match was admitted on
                # the already completed refresh worker.
                from cifereca.gui.borrower_public_c_bridge import (
                    exact_loan_public_c,
                )
                receipt = exact_loan_public_c(self, loan)
                if receipt is not None:
                    item._cover_document_ref = receipt.document
                    item._cover_borrower_public_c_receipt = receipt
                    item._borrower_public_c_source = loan
                    item._borrower_public_c_context = scope_context
        # MasterDetailPage.show_rows has an append-only fast path when keys
        # stay identical. A new exact local cover for the same loan must
        # invalidate that presentation (and repaint the mapped Gtk.ListView),
        # without changing the loan SourceKey or mutating its original rights.
        item._loan_cover_context = scope_context
        item._public_row_content = (
            "loan", source_key, loan.title, subtitle, meta, due.label,
            display.thumbnail_url, due.style, scope_context,
        )
        if evidence is not None:
            # Do not run the URL-only in-place repaint optimization when a
            # documentary association changes. Force cell rebind at N2.
            item._public_row_content += (evidence,)
        elif getattr(item, "_cover_loan_supplier_evidence", None) is not None:
            item._public_row_content += (item._cover_loan_supplier_evidence,)
        elif getattr(item, "_cover_borrower_public_c_receipt", None) is not None:
            # A stable loan key does not imply a stable public C epoch.
            item._public_row_content += (
                item._cover_borrower_public_c_receipt.document,
                item._cover_borrower_public_c_receipt.epoch,
            )
        ranked_rows.append((due.sort_key, item))
    ranked_rows.sort(key=lambda item: item[0])
    rows = [row for _rank, row in ranked_rows]
    if hasattr(self, "loan_scope_count"):
        self.loan_scope_count.set_label(
            loan_context_label(
                filter_key=self.loan_filter,
                visible_count=len(rows),
                medium_count=counts.get(self.loan_filter, counts["all"]),
                total_count=counts["all"],
                language_filtered=bool(wanted_languages),
            )
        )
    objects={key:{"source_provider":loan.source_provider,"media_format":loan.media_format,"author":loan.author,"metadata":(dict((loan.raw or {}).get('_cifereca') or {}) if isinstance((loan.raw or {}).get('_cifereca'),dict) else dict(loan.raw or {}))} for key,loan in self.loan_objects.items()}
    self._attach_row_facets(rows,objects,self.search_controls)
    changed_cover_keys = None
    revision = getattr(self, "_loan_cover_decision_revision", None)
    if (type(revision) is int and revision > 0
            and getattr(self, "_loan_cover_in_place_revision", None) == revision):
        from cifereca.gui.loan_cover_repaint import repaint_exact_loan_cover_rows
        changed_cover_keys = repaint_exact_loan_cover_rows(
            self.loan_page, rows, context=scope_context,
        )
    self._loan_cover_in_place_revision = None
    # All visible fields and exact context unchanged: MasterDetailPage's
    # append-fast path now keeps the selected Gtk.ListView cells. Otherwise
    # its existing full replacement still handles sorting/filter changes.
    self.loan_page.show_rows(rows, total_count=len(rows))
    if changed_cover_keys:
        selected = getattr(getattr(self.loan_page, "selection", None),
                           "get_selected_item", None)
        selected_row = selected() if callable(selected) else None
        selected_key = getattr(selected_row, "key", None)
        current = getattr(self, "_displayed_loan", None)
        fresh = getattr(self, "loan_objects", {}).get(selected_key)
        if (selected_key in changed_cover_keys
                and type(current) is Loan and type(fresh) is Loan
                and current.identity_id == identity_id
                and fresh.identity_id == identity_id
                and loan_source_key(current) == selected_key
                and loan_source_key(fresh) == selected_key
                and getattr(self.service, "_account_context_epoch", None)
                    == scope_context[1]):
            # The list signal cannot update an already-open DetailPanel.
            # Refresh its presentation from the SAME cached N1/N2 decision:
            # no provider I/O, no action or selected-row mutation.
            from cifereca.gui.loan_cover_n2_bridge import show_attested_loan_cover
            if not show_attested_loan_cover(
                self, fresh, lambda: _loan_display_cover(self, fresh),
            ):
                from cifereca.gui.loan_supplier_document_cover_bridge import show_supplier_loan_cover
                if not show_supplier_loan_cover(
                    self, fresh, lambda: _loan_display_cover(self, fresh),
                ):
                    from cifereca.gui.borrower_public_c_bridge import show_public_loan_c
                    if not show_public_loan_c(
                        self, fresh, lambda: _loan_display_cover(self, fresh),
                    ):
                        self.loan_page.detail.show_loan(_loan_display_cover(self, fresh))


def _loan_selected(self, row: BookListItem | None) -> None:
    if row is None: return
    loan = self.loan_objects.get(row.key)
    if loan:
        self._displayed_loan = loan
        from cifereca.gui.loan_cover_n2_bridge import show_attested_loan_cover
        if not show_attested_loan_cover(
            self, loan, lambda: _loan_display_cover(self, loan),
        ):
            from cifereca.gui.loan_supplier_document_cover_bridge import show_supplier_loan_cover
            if not show_supplier_loan_cover(
                self, loan, lambda: _loan_display_cover(self, loan),
            ):
                from cifereca.gui.borrower_public_c_bridge import show_public_loan_c
                if not show_public_loan_c(
                    self, loan, lambda: _loan_display_cover(self, loan),
                ):
                    self.loan_page.detail.show_loan(_loan_display_cover(self, loan))
        self._add_local_book_actions(self.loan_page.detail,{'source_provider':loan.source_provider,
            'source_credential':loan.source_credential,'record_id':loan.record_id})
        self._add_facets(self.loan_page.detail, {'source_provider':loan.source_provider,
            'author':loan.author,'media_format':loan.media_format}, self.search_controls)


def refresh_loans(self, force: bool = True, control: Gtk.Widget | None = None) -> None:
    # Pin the service and explicit identity before TaskRunner queues the GET.
    # A later MainWindow service rebind must not execute this request for B.
    service = self.service
    identity = service.current_identity
    identity_id = identity.identity_id if identity else None
    if type(identity_id) is not str or not identity_id.strip():
        return
    from cifereca.core.source_context import source_context_epoch
    admitted_source_epoch = source_context_epoch(service)
    # An A -> B -> A round trip has the same identity ID but invalidates an
    # earlier refresh. Guard the GTK callback by the Core account epoch too.
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    library_revision = getattr(self, "_loan_cover_library_revision", 0)
    def work():
        rows = service.refresh_identity_loans(
            force=force, identity_id=identity_id,
            expected_source_epoch=admitted_source_epoch,
            publication_epoch=account_epoch,
        )
        # A separate, source-exact, PUBLIC catalogue C may supply the
        # thumbnail of an image-less loan, but never the loan right.
        # This is the existing bounded background worker, not GTK.
        from cifereca.core.notice_c_borrower_public_projection import (
            attach_borrower_public_c, borrower_public_c_descriptors,
        )
        # Headless worker doubles have no storage. Public-cover enrichment
        # is optional; a successful private loan refresh must survive it.
        # Production services expose storage and still require exact C proof.
        storage = getattr(service, "storage", None)
        if storage is not None:
            descriptors = borrower_public_c_descriptors(service, source_epoch=source_epoch)
            # #3724: the existing refresh can repair a missing initial C
            # from already admitted public cache evidence only.
            from time import time
            from cifereca.core.notice_c_enrichment_intent import (
                ExistingLoanIntent, request_notice_enrichment,
            )
            request_notice_enrichment(
                storage, ExistingLoanIntent(
                    service, rows, identity_id, account_epoch, source_epoch, descriptors,
                ), now=int(time()),
            )
            attach_borrower_public_c(
                storage, rows, identity_id=identity_id,
                account_epoch=account_epoch, source_epoch=source_epoch,
                descriptors=descriptors,
            )
        # Resolve a missing loan cover only on this existing background
        # worker, once per refresh. Core's library_entries(scan=False) reads
        # local placements/entitlements; it never contacts a catalogue,
        # authenticates a borrower or acquires a file.
        cover_rows = ()
        if any(type(loan) is Loan and loan.identity_id == identity_id
               and not loan.thumbnail_url for loan in rows):
            try:
                cover_rows = materialized_loan_cover_rows(
                    service.library_entries(identity_id=identity_id, scan=False)
                )
            except (OSError, RuntimeError, ValueError):
                # Cover enrichment is optional. A valid loan inventory must
                # remain visible if a local read/placement check is unavailable.
                cover_rows = ()
        return rows, cover_rows

    def done(payload):
        # Only display in the same Core/UI owner; the completed provider
        # read is not cancelled merely because navigation changed.
        if getattr(self, "_closed", False) or self.service is not service:
            return
        current = service.current_identity
        if (current.identity_id if current else None) == identity_id:
            if (getattr(service, "_account_context_epoch", None) != account_epoch
                    or getattr(service, "_source_context_epoch", None) != source_epoch):
                return
            rows, cover_rows = payload
            if getattr(self, "_loan_cover_library_revision", 0) == library_revision:
                self._loan_cover_projection = (account_epoch, identity_id, cover_rows)
            self._loans_refreshed(rows)
    self.runner.submit(
        "Actualisation des emprunts…",
        work,
        done,
        control=control,
        priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.ACTIVE_LOAN,
        kind="loans",
        origin="user" if control is not None else "automatic",
    )


def _loans_refreshed(self, rows):
    displayed=getattr(self, '_displayed_loan', None)
    self._populate_loans(rows)
    if displayed:
        # A weak (id, provider, credential, account) lookup could rebind an
        # open detail after refresh to another backend, record or medium.
        # Resolve only from the same current-identity-filtered snapshot that
        # _populate_loans installed for the visible loan rows.
        key = loan_source_key(displayed)
        matches = [
            loan for loan in getattr(self.service, "last_loans", ())
            if type(loan) is Loan and loan_source_key(loan) == key
        ]
        fresh = matches[0] if len(matches) == 1 else None
        self._displayed_loan=fresh
        if fresh:
            from cifereca.gui.loan_cover_n2_bridge import show_attested_loan_cover
            if not show_attested_loan_cover(
                self, fresh, lambda: _loan_display_cover(self, fresh),
            ):
                from cifereca.gui.loan_supplier_document_cover_bridge import show_supplier_loan_cover
                if not show_supplier_loan_cover(
                    self, fresh, lambda: _loan_display_cover(self, fresh),
                ):
                    self.loan_page.detail.show_loan(_loan_display_cover(self, fresh))
        else:self.loan_page.detail.empty(
            "Prêt non retrouvé dans cette actualisation",
            "La réponse peut être vide ou partielle ; la disparition du prêt "
            "chez le fournisseur n’est pas confirmée. Les fichiers locaux sont conservés.",
        )
    if rows:
        self.toast(f"{len(rows)} emprunt(s) actualisé(s)")
    else:
        # A zero-row response is not proof that a library has zero loans:
        # an identity mismatch or a provider-side partial response is possible.
        self.toast("Aucun emprunt reçu · Vérifiez l’identité, les sources et les filtres")


def confirm_renew(self, loan: Loan, control: Gtk.Widget) -> None:
    # Consent applies to the loan *selected when the dialog opens*, not a
    # mutable DTO reused by a later refresh or identity switch.
    selected = copy(loan)
    owner = getattr(getattr(self.service, "current_identity", None), "identity_id", None)
    generation = getattr(self.service, "_account_context_epoch", None)
    dialog = Adw.MessageDialog.new(
        self, "Prolonger ce prêt ?",
        "Une demande sera envoyée au fournisseur pour ce compte et ce prêt. "
        "La réponse ne garantit pas encore la nouvelle date d'échéance.",
    )
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("ok", "Demander la prolongation")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

    def on_response(_dialog, response):
        if response != "ok":
            return
        current = getattr(getattr(self.service, "current_identity", None), "identity_id", None)
        if (current != owner or owner != selected.identity_id
                or getattr(self.service, "_account_context_epoch", None) != generation):
            self.show_attention(
                "Le compte a changé : sélectionnez de nouveau le prêt.",
                code="loan_scope_changed",
            )
            return
        self._renew(selected, control)

    dialog.connect("response", on_response)
    dialog.present()


def _renew_result(self, result: Any) -> None:
    kind, message, code = loan_renewal_feedback(result)
    # A fresh inventory can display the new due date but cannot rewrite the
    # durable remote-attempt receipt or auto-submit a second RenewLoans POST.
    if kind == "submitted":
        self.toast(message, 6)
        self.refresh_loans(True)
    else:
        self.show_attention(message, code=code)
        if type(result) is dict and result.get("status") == "verification_unavailable":
            self.refresh_loans(True)


def _renew(self, loan: Loan, control: Gtk.Widget) -> None:
    """One admitted loan/owner; late navigation changes presentation only."""
    service = self.service
    identity = service.current_identity
    owner = getattr(identity, "identity_id", None)
    account_epoch = getattr(service, "_account_context_epoch", None)
    source_epoch = getattr(service, "_source_context_epoch", None)
    ui_identity_epoch = getattr(self, "_identity_context_generation", None)
    ui_account_epoch = getattr(self, "_account_context_generation", None)

    def view_current() -> bool:
        try:
            return bool(
                not getattr(self, "_closed", False)
                and self.service is service
                and service.current_identity is identity
                and type(owner) is str and owner
                and type(loan) is Loan and loan.identity_id == owner
                and type(account_epoch) is int and account_epoch >= 0
                and type(source_epoch) is int and source_epoch >= 0
                and service._account_context_epoch == account_epoch
                and service._source_context_epoch == source_epoch
                and getattr(self, "_identity_context_generation", None)
                    == ui_identity_epoch
                and getattr(self, "_account_context_generation", None)
                    == ui_account_epoch
            )
        except Exception:
            return False

    if not view_current():
        return
    capture = getattr(service, "capture_admitted_loan_renewal", None)
    work = lambda: service.renew(loan, confirmed=True)
    if callable(capture):
        try:
            admission = capture(loan)
        except Exception:
            admission = None
        if admission is None:
            if view_current():
                self._renew_result({
                    "status": "blocked", "code": "loan_scope_unverified",
                })
            return
        renew_admitted = getattr(service, "renew_admitted_loan", None)
        if not callable(renew_admitted):
            return
        work = lambda: renew_admitted(loan, confirmed=True, admission=admission)

    def completed(result: Any) -> None:
        # Only the original loans view can show the renewal receipt or
        # refresh its selected identity. The Core-owned one-shot request
        # is not reinterpreted or cancelled by GTK navigation.
        if view_current():
            self._renew_result(result)

    self.runner.submit(
        "Demande de prolongation…",
        work,
        completed,
        control=control, priority=TaskPriority.USER_ACTION, kind="loan-renew",
    )

