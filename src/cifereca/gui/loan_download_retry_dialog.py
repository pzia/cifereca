"""Identity-scoped read-only download retry report and explicit local budget reset."""
from __future__ import annotations

from datetime import datetime

from ..core.loan_download_retry import FAILURE_LIMIT
from ..core.loan_download_retry_review import (
    review_loan_download_retries, reset_reviewed_loan_download_retry,
)
from .sync_action_scope import sync_action_scope_current
from ..core.source_context import source_context_epoch
from ..plugin_api.page_lifecycle import PageViewContext
from .task_runner import TaskPriority


def open_loan_download_retry_dialog(window, scope, *, Gtk, Adw, control):
    service = window.service
    def current():
        return window.service is service and sync_action_scope_current(window, scope)

    return _open_retry_dialog(window, service, scope.identity_id, current,
                              window._refresh_sync_page, Gtk=Gtk, Adw=Adw, control=control)


def add_maintenance_retry_entry(window, box, context, *, Gtk, Adw):
    """A second entry point, not a second queue or retry policy."""
    try:
        service, runner = window.service, window.runner
        owner = service.current_identity
        identity = owner.identity_id
        generation = window._identity_context_generation
        epoch = source_context_epoch(service)
        mount = window._maintenance_mount_owner
        disposers = window._guided_maintenance_disposers
        if (type(context) is not PageViewContext or context.route != "tools"
                or type(identity) is not str or not identity.strip()
                or context.identity_id != identity or type(generation) is not int
                or mount is None):
            return False
    except Exception:
        return False
    disposed = False
    opened = 0

    def current():
        try:
            return (not disposed and not window._closed and window.service is service
                    and window.runner is runner and service.current_identity is owner
                    and owner.identity_id == identity
                    and window._identity_context_generation == generation
                    and source_context_epoch(service) == epoch
                    and window._maintenance_mount_owner is mount
                    and window._guided_maintenance_disposers is disposers
                    and context.is_current())
        except Exception:
            return False

    if not current():
        return False
    group, row = Adw.PreferencesGroup(), Adw.ActionRow()
    row.set_title("Téléchargements à reprendre")
    row.set_subtitle("Même file que Synchronisation ; reprise individuelle des tentatives admissibles.")
    button = window.action_button("Examiner…", "view-refresh-symbolic")
    row.add_suffix(button)
    group.add(row)

    def clicked(control):
        nonlocal opened
        if not current() or control.get_root() is None:
            return
        opened += 1
        lease = opened
        _open_retry_dialog(window, service, identity,
                           lambda: current() and lease == opened,
                           lambda: None, Gtk=Gtk, Adw=Adw, control=control)

    def dispose():
        nonlocal disposed
        disposed = True

    button.connect("clicked", clicked)
    disposers.append(dispose)
    box.append(group)
    return True


def _open_retry_dialog(window, service, identity_id, current, after_reset, *, Gtk, Adw, control):
    if not current():
        return

    def read():
        if not current():
            return None
        try:
            return review_loan_download_retries(service, identity_id=identity_id)
        except Exception:
            return None

    def show(rows):
        if not current():
            return
        if rows is None:
            body = "Lecture indisponible. Actualisez l’affichage pour réessayer."
        elif not rows:
            body = "Aucun échec de téléchargement enregistré pour cette identité."
        else:
            paused = sum(row.can_reset for row in rows)
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
        state = ("Ressource inactive ou non attestée" if row.inactive else
                 "État local à vérifier" if row.local_state_invalid else
                 "Intervention ADEPT requise" if row.runtime_hold and row.runtime_phase == "purchase_acsm" else
                 "Intervention runtime requise" if row.runtime_hold else
                 "Intervention ADEPT requise" if row.adept_hold or row.profile_refused_at else
                 "En pause" if row.retry.paused else "À retenter")
        failed_at = row.retry.last_failed_at
        if row.local_state_invalid:
            reason = "État local illisible. Reprise manuelle de ce titre indisponible ; vérifiez les diagnostics."
        elif row.runtime_hold:
            reason = (
                "Résultat du fulfillment ADEPT de l’achat incertain. Vérifiez les diagnostics."
                if row.runtime_phase == "purchase_acsm" else
                "Résultat du runtime incertain. Rejeu non qualifié ; vérifiez les diagnostics."
            )
            failed_at = row.runtime_started_at
        elif row.adept_hold:
            reason = "Résultat du fulfillment ADEPT incertain. Vérifiez les diagnostics."
        elif row.profile_refused_at:
            reason = "Vérifiez le profil ADEPT dans les paramètres de l’identité."
            failed_at = row.profile_refused_at
        else:
            reason = {
                "canonical_delivery_failed": "Échec de livraison",
                "drm_unresolved": "DRM non résolu",
            }[row.retry.last_reason]
        source = row.source.scope
        try:
            when = (datetime.fromtimestamp(failed_at).strftime("%d/%m/%Y %H:%M")
                    if failed_at else "Date inconnue")
        except (OSError, ValueError, OverflowError):
            when = "Date inconnue"
        attempts = ("Échecs indéterminés" if row.local_state_invalid else
                    f"{row.retry.failures}/{FAILURE_LIMIT} échec(s)")
        label = Gtk.Label(
            label=(f"{source.provider_id} · compte {source.account_id or 'principal'} "
                   f"· connexion {source.credential_id}\n"
                   f"{state} · {attempts}\n"
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
                    if not current():
                        return False
                    try:
                        reset_reviewed_loan_download_retry(
                            service, identity_id=identity_id, row=row,
                        )
                        return True
                    except Exception:
                        return False
                def done(ok):
                    if current():
                        dialog.close()
                        window.toast("Tentatives réinitialisées" if ok is True else
                                     "Reprise indisponible : actualisez l’affichage")
                        after_reset()
                window.runner.submit(
                    "Réinitialisation des tentatives…", work, done, control=button,
                    priority=TaskPriority.USER_ACTION, kind="source-sync",
                )
            button.connect("clicked", clicked)
            panel.append(button)
        box.append(panel)

    window.runner.submit("Lecture des téléchargements…", read, show, control=control,
                         priority=TaskPriority.USER_ACTION, kind="source-sync")


def refresh_loan_download_retry_summary(window, scope, label):
    """Update only the active page generation, using the ordinary worker."""
    service = window.service
    if not hasattr(service, "storage"):
        return
    def read():
        try:
            return review_loan_download_retries(service, identity_id=scope.identity_id)
        except Exception:
            return None
    def done(rows):
        if (window.service is not service or not sync_action_scope_current(window, scope)
                or getattr(window, "_sync_retry_summary", None) is not label):
            return
        if rows is None:
            label.set_label("Téléchargements à reprendre : état indisponible")
            label.set_visible(True)
            return
        active = [row for row in rows if not row.inactive]
        holds = sum(bool(row.adept_hold or row.profile_refused_at or row.runtime_hold or row.local_state_invalid) for row in active)
        paused = sum(row.can_reset for row in active)
        retrying = len(active) - holds - paused
        label.set_label(
            f"Téléchargements : {paused} en pause · {retrying} à retenter · "
            f"{holds} intervention(s) requise(s)"
        )
        label.set_visible(bool(active))
    window.runner.submit("État des téléchargements…", read, done,
                         priority=TaskPriority.NORMAL, kind="loan-download-review",
                         origin="automatic")
