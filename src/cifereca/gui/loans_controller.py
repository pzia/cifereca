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
from ..core.loan_source_identity import loan_source_key
from .book_widgets import BookListItem
from .loan_view import LOAN_FILTER_LABELS, loan_context_label, loan_due_presentation, loan_medium_counts, loan_row_metadata
from .master_detail import MasterDetailPage
from .query_controls import MultiSelect
from .task_runner import TaskPriority
from .return_feedback import loan_return_feedback
from .loan_renewal_feedback import loan_renewal_feedback
from .detail_model import fr_date


def _build_loans_page(self) -> MasterDetailPage:
    page = MasterDetailPage(self, "Mes emprunts", "Prêts physiques et numériques", "Aucun emprunt affiché", "Vérifiez l’identité et les filtres, puis actualisez les emprunts. Une liste vide ne prouve pas l’absence de prêts chez le fournisseur.")
    history = Gtk.Button(label="Historique"); history.connect("clicked", lambda _b: self.navigate("history")); page.header_actions.append(history)
    bookings = Gtk.Button(label="Réservations"); bookings.connect("clicked", lambda _b: self.navigate("bookings")); page.header_actions.append(bookings)
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
            and identity_id is not None and row.identity_id == identity_id]
    self.service.last_loans = rows
    # The same upstream loan can be visible under two Paris credentials.
    # Preserve both source-bound rows instead of overwriting by loan.id.
    self.loan_objects = {loan_source_key(x): x for x in rows}
    self._render_loans()
    self._render_kobo_loans()
    if hasattr(self,'_refresh_statistics'):self._refresh_statistics()


def _render_loans(self) -> None:
    if not hasattr(self, "loan_page"):
        return
    # Service cache/async writers can be updated independently of GTK; never
    # trust a mixed-identity last_loans projection at display time.
    identity_id = getattr(getattr(self.service, "current_identity", None), "identity_id", None)
    all_loans = [loan for loan in self.service.last_loans
                 if type(loan) is Loan and identity_id is not None
                 and loan.identity_id == identity_id]
    counts = loan_medium_counts(all_loans)
    if hasattr(self, "loan_filter_buttons"):
        for key, button in self.loan_filter_buttons.items():
            label = LOAN_FILTER_LABELS.get(key, key)
            button.set_label(f"{label} · {counts.get(key, 0)}")
    ranked_rows: list[tuple[tuple[int, int], BookListItem]] = []
    wanted_languages = set(self.loan_languages.values()) if hasattr(self, 'loan_languages') else set()
    for loan in all_loans:
        if self.loan_filter != "all" and loan.medium != self.loan_filter:
            continue
        raw = dict(loan.raw or {})
        meta = dict(raw.get('_cifereca') or {}) if isinstance(raw.get('_cifereca'), dict) else raw
        loan_languages = self._known_item_languages(loan.source_provider, loan.record_id or loan.id, meta)
        if wanted_languages and not loan_languages.intersection(wanted_languages):
            continue
        due = loan_due_presentation(loan.due_at, is_late=bool(loan.is_late))
        subtitle = loan.library or ("Prêt numérique" if loan.medium == "digital" else "Prêt physique")
        # BnF (and other providers) can supply a verified author on the
        # exact loan. Display that author without borrowing names from a
        # homonymous catalogue record or another loan's thumbnail.
        if type(loan.author) is str and loan.author.strip():
            subtitle = loan.author.strip() + " · " + subtitle
        meta = loan_row_metadata(loan.due_at, due, document_type=loan.document_type, provider=loan.provider)
        ranked_rows.append((
            due.sort_key,
            BookListItem(
                loan_source_key(loan),
                loan.title,
                subtitle,
                meta,
                due.label,
                loan.thumbnail_url,
                badge_style=due.style,
            ),
        ))
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
    self.loan_page.show_rows(rows, total_count=len(rows))


def _loan_selected(self, row: BookListItem | None) -> None:
    if row is None: return
    loan = self.loan_objects.get(row.key)
    if loan:
        self._displayed_loan = loan
        self.loan_page.detail.show_loan(loan)
        self._add_local_book_actions(self.loan_page.detail,{'source_provider':loan.source_provider,
            'source_credential':loan.source_credential,'record_id':loan.record_id})
        self._add_facets(self.loan_page.detail, {'source_provider':loan.source_provider,
            'author':loan.author,'media_format':loan.media_format}, self.search_controls)


def refresh_loans(self, force: bool = True, control: Gtk.Widget | None = None) -> None:
    identity = self.service.current_identity
    identity_id = identity.identity_id if identity else None
    # An A -> B -> A round trip has the same identity ID but invalidates an
    # earlier refresh. Guard the GTK callback by the Core account epoch too.
    account_epoch = getattr(self.service, "_account_context_epoch", None)
    def done(rows):
        current = self.service.current_identity
        if (current.identity_id if current else None) == identity_id:
            if getattr(self.service, "_account_context_epoch", None) != account_epoch:
                return
            self._loans_refreshed(rows)
    self.runner.submit(
        "Actualisation des emprunts…",
        lambda: self.service.refresh_identity_loans(force=force, identity_id=identity_id),
        done,
        control=control,
        priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.ACTIVE_LOAN,
        kind="loans",
    )


def _loans_refreshed(self, rows):
    displayed=getattr(self, '_displayed_loan', None)
    self._populate_loans(rows)
    if displayed:
        fresh=next((r for r in rows if (r.id,r.source_provider,r.source_credential,r.source_account_id)==
                    (displayed.id,displayed.source_provider,displayed.source_credential,displayed.source_account_id)),None)
        self._displayed_loan=fresh
        if fresh:self.loan_page.detail.show_loan(fresh)
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
    self.runner.submit(
        "Demande de prolongation…",
        lambda: self.service.renew(loan, confirmed=True),
        self._renew_result,
        control=control, priority=TaskPriority.USER_ACTION, kind="loan-renew",
    )


def confirm_return(self, loan: Loan, control: Gtk.Widget) -> None:
    if str(loan.source_provider).lower() != "paris" or loan.medium != "digital":
        dialog = Adw.MessageDialog.new(self, "Rendre ce livre numérique ?", "Confirmer la demande de restitution au fournisseur.")
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("provider", "Restituer")
        dialog.set_response_appearance("provider", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, response: self._return_loan_pnb(loan, control)
            if response == "provider" else None,
        )
        dialog.present()
        return

    dialog = Adw.MessageDialog.new(
        self, "Restituer ce prêt numérique ?",
        "ADEPT restitue la licence Adobe d’origine, puis vérifie Paris. "
        "Paris (PNB) est une action séparée, qui ne peut être déclenchée automatiquement.",
    )
    dialog.add_response("cancel", "Annuler")
    dialog.add_response("adept", "Restituer la licence ADEPT")
    dialog.add_response("pnb", "Restituer à Paris (PNB)")
    dialog.set_response_appearance("adept", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_response_appearance("pnb", Adw.ResponseAppearance.DESTRUCTIVE)

    def on_response(_dialog, response):
        if response == "adept":
            self._return_loan(loan, control)
        elif response == "pnb":
            self._return_loan_pnb(loan, control)

    dialog.connect("response", on_response)
    dialog.present()


def _return_loan_result(self, result: Any, *, channel: str) -> None:
    """The background task completed; the loan return may still be partial."""
    self.refresh_loans(True)
    kind, message = loan_return_feedback(result, channel=channel)
    if kind == "confirmed":
        self.toast(message, 6)
    else:
        code = result.get("code") if isinstance(result, dict) else None
        self.show_attention(message, code=str(code or "loan_return_unconfirmed"))


def _return_loan(self, loan: Loan, control: Gtk.Widget) -> None:
    self.runner.submit(
        "Vérification de restitution ADEPT…",
        lambda: self.service.return_loan_prefer_adept(loan),
        lambda result: self._return_loan_result(result, channel="adept"),
        control=control,
        priority=TaskPriority.USER_ACTION, kind="loan-return",
    )


def _return_loan_pnb(self, loan: Loan, control: Gtk.Widget) -> None:
    self.runner.submit(
        "Vérification de restitution Paris (PNB)…",
        lambda: self.service.return_digital(loan, explicit_retry=True),
        lambda result: self._return_loan_result(result, channel="pnb"),
        control=control,
        priority=TaskPriority.USER_ACTION, kind="loan-return",
    )
