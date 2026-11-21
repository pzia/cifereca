"""Identity-scoped read-only loan retry report and explicit local budget reset."""
from __future__ import annotations

from datetime import datetime

from ..core.loan_download_retry import FAILURE_LIMIT
from ..core.loan_download_retry_review import (
    review_loan_download_retries, reset_reviewed_loan_download_retry,
)
from .sync_action_scope import sync_action_scope_current
from .task_runner import TaskPriority


def open_loan_download_retry_dialog(window, scope, *, Gtk, Adw, control):
    service = window.service
    def current():
        return window.service is service and sync_action_scope_current(window, scope)

    def read():
        try:
            return review_loan_download_retries(service, identity_id=scope.identity_id)
        except Exception:
            return None

    def show(rows):
        if not current():
            return
        if rows is None:
            body = "Lecture indisponible. Actualisez l’affichage pour réessayer."
        elif not rows:
            body = "Aucun échec de téléchargement de prêt enregistré pour cette identité."
        else:
            paused = sum(row.retry.paused for row in rows)
            body = f"{len(rows)} téléchargement(s) à vérifier, dont {paused} en pause."
        dialog = Adw.MessageDialog.new(window, "Téléchargements à reprendre", body)
        dialog.add_response("close", "Fermer")
        if rows:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_max_content_height(420)
            scroll.set_propagate_natural_height(True)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            scroll.set_child(box)
            for row in rows:
                render_row(box, row, dialog)
            dialog.set_extra_child(scroll)
        dialog.present()

    def render_row(box, row, dialog):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        title = Gtk.Label(label=row.title, xalign=0)
        title.set_wrap(True)
        title.add_css_class("heading")
        panel.append(title)
        state = ("Prêt inactif" if row.inactive else
                 "Intervention ADEPT requise" if row.adept_hold else
                 "En pause" if row.retry.paused else "À retenter")
        reason = {
            "canonical_delivery_failed": "Échec de livraison",
            "drm_unresolved": "DRM non résolu",
        }.get(row.retry.last_reason, "Tentative ADEPT incertaine")
        source = row.source.scope
        when = (datetime.fromtimestamp(row.retry.last_failed_at).strftime("%d/%m/%Y %H:%M")
                if row.retry.last_failed_at else "Date inconnue")
        label = Gtk.Label(
            label=(f"{source.provider_id} · compte {source.account_id or 'principal'} "
                   f"· connexion {source.credential_id}\n"
                   f"{state} · {row.retry.failures}/{FAILURE_LIMIT} échec(s)\n"
                   f"{reason} · {when}"),
            xalign=0,
        )
        label.set_wrap(True)
        panel.append(label)
        if row.can_reset:
            button = Gtk.Button(label="Réessayer à la prochaine synchronisation")
            def clicked(_button):
                if not current():
                    return
                def work():
                    try:
                        reset_reviewed_loan_download_retry(
                            service, identity_id=scope.identity_id, row=row,
                        )
                        return True
                    except Exception:
                        return False
                def done(ok):
                    if current():
                        dialog.close()
                        window.toast("Tentatives réinitialisées" if ok is True else
                                     "Reprise indisponible : actualisez l’affichage")
                window.runner.submit(
                    "Réinitialisation des tentatives…", work, done, control=button,
                    priority=TaskPriority.USER_ACTION, kind="source-sync",
                )
            button.connect("clicked", clicked)
            panel.append(button)
        box.append(panel)

    window.runner.submit("Lecture des téléchargements…", read, show, control=control,
                         priority=TaskPriority.USER_ACTION, kind="source-sync")
