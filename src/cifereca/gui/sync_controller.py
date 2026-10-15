"""GTK synchronization page and runner orchestration (#1131).

This module delegates exclusively to existing service/broker contracts, and
uses MainWindow as a host without importing it or starting threads of its own.
"""
from __future__ import annotations

from typing import Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from ..plugin_api import SyncRequest
from .action_widgets import AdaptiveActionBar, add_css, clear_box
from .sync_view import (
    sync_identity_action_label, sync_source_presentation,
    sync_source_row_labels,
    sync_local_preflight_presentation, sync_local_reconciliation_presentation,
)
from .task_runner import TaskPriority
from .sync_action_scope import capture_sync_action_scope, sync_action_scope_current


def _build_sync_page(self):
    if getattr(self, "_closed", False):
        return Gtk.Label(label="Synchronisation indisponible")
    page_token = object()
    self._sync_page_token = page_token
    scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for edge in ("top", "bottom", "start", "end"):
        getattr(outer, "set_margin_" + edge)(20)
    heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = Gtk.Label(label="Synchronisation", xalign=0); title.set_hexpand(True); add_css(title, "page-title")
    actions = AdaptiveActionBar(source=heading)
    refresh = self.action_button("Actualiser", "view-refresh-symbolic")
    all_now = self.action_button("Synchroniser les sources", "emblem-synchronizing-symbolic", suggested=True)
    self._sync_all_button = all_now
    actions.append(refresh); actions.append(all_now)
    heading.append(title); heading.append(actions); outer.append(heading)
    scope_note = Gtk.Label(
        label="Actualiser les sources met à jour les notices et droits en ligne. "
        "Ce bouton ne télécharge aucun ebook. L’acquisition locale Kobo est "
        "une opération distincte qui requiert un compte et une confirmation explicites.",
        xalign=0,
    )
    scope_note.set_wrap(True)
    outer.append(scope_note)
    self._sync_sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); outer.append(self._sync_sources_box)
    refresh.connect(
        "clicked",
        lambda _b: self._refresh_sync_page()
        if capture_sync_action_scope(self, page_token=page_token) is not None
        else None,
    )
    def sync_all(button):
        # This button must never silently turn a missing identity into the
        # multi-account SyncRequest(identity_id=None) default.
        scope = capture_sync_action_scope(self, page_token=page_token)
        if scope is None:
            return
        broker = self.plugin_manager.broker("sync") if self.plugin_manager else None
        if broker is None or not sync_action_scope_current(self, scope):
            return
        request = SyncRequest(identity_id=scope.identity_id)

        def completed(_result):
            if not sync_action_scope_current(self, scope):
                return
            self._refresh_sync_page()
            if sync_action_scope_current(self, scope):
                self.refresh_library(False)

        self.runner.submit(
            "Synchronisation des sources…",
            lambda: broker.synchronize(request)
            if sync_action_scope_current(self, scope) else None,
            completed,
            control=button,
            priority=TaskPriority.USER_ACTION,
            kind="source-sync",
        )
    all_now.connect("clicked", sync_all)
    scroll.set_child(outer); return scroll


def _refresh_sync_page(self) -> None:
    if getattr(self, "_closed", False):
        return
    box = getattr(self, "_sync_sources_box", None)
    if box is None: return
    clear_box(box)
    if getattr(self, "_closed", False):
        return
    page_token = getattr(self, "_sync_page_token", None)
    self._sync_preview_generation = getattr(self, "_sync_preview_generation", 0) + 1
    preview_generation = self._sync_preview_generation
    broker = self.plugin_manager.broker("sync") if self.plugin_manager else None
    identity = self.service.current_identity
    all_now = getattr(self, "_sync_all_button", None)
    global_label = sync_identity_action_label(identity)
    if all_now is not None:
        all_now._cifereca_action_label = global_label
        child = all_now.get_child()
        if child is not None and callable(getattr(child, "set_label", None)):
            child.set_label(global_label)
        else:
            all_now.set_label(global_label)
        all_now.set_sensitive(False)
    if broker is None:
        if all_now is not None:
            all_now.set_tooltip_text("Synchronisation indisponible : broker absent")
        box.append(Gtk.Label(label="Broker de synchronisation indisponible", xalign=0)); return
    # A page showing no identity cannot expose per-account sync controls.
    scope = capture_sync_action_scope(
        self, page_token=page_token, preview_generation=preview_generation,
    )
    if scope is None:
        if all_now is not None:
            all_now.set_sensitive(False)
        box.append(Gtk.Label(
            label="Sélectionnez une identité avant de synchroniser.",
            xalign=0,
        ))
        return
    rows = broker.sources(scope.identity_id)
    if not sync_action_scope_current(self, scope):
        return
    # Never render or enable another identity's broker row.
    rows = [
        source for source in rows
        if getattr(source, "identity_id", None) == scope.identity_id
    ]
    presentations = [sync_source_presentation(source) for source in rows]
    # Number genuinely indistinguishable same-provider connections without
    # exposing any private credential/account/source ID. Unique titles stay
    # unchanged to preserve existing keyboard navigation and accessible names.
    row_titles = sync_source_row_labels(rows)
    actionable = any(item.action_enabled for item in presentations)
    if all_now is not None:
        all_now.set_sensitive(actionable)
        all_now.set_tooltip_text(
            global_label if actionable
            else "Aucune source activée, connectée et synchronisable pour cette identité"
        )
    if not rows:
        box.append(Gtk.Label(label="Aucune source synchronisable pour cette identité", xalign=0)); return
    for source, presentation, display_label in zip(rows, presentations, row_titles):
        if not sync_action_scope_current(self, scope):
            return
        # Refuse a broker row whose ownership does not match the explicitly
        # selected profile. No toggle/preview may be wired to that row.
        if getattr(source, "identity_id", None) != scope.identity_id:
            continue
        # The scoped broker order determines ephemeral labels only; sid remains
        # the opaque, separate source route for each row.
        group = Adw.PreferencesGroup(); box.append(group)
        row = Adw.ActionRow(); row.set_title(display_label)
        row.set_subtitle(presentation.summary)
        row.set_subtitle_lines(0)
        toggle = Gtk.Switch(); toggle.set_active(source.enabled); toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_tooltip_text("Activation : inclure cette source dans les synchronisations")
        def toggled(widget, _param, sid=source.source_id):
            if not sync_action_scope_current(self, scope):
                return
            try:
                broker.set_enabled(sid, widget.get_active())
                if sync_action_scope_current(self, scope):
                    self._refresh_sync_page()
            except Exception:
                if sync_action_scope_current(self, scope):
                    # Exceptions from provider hooks may contain credentials.
                    self.show_error(
                        "Modification de la source indisponible.",
                        "Synchronisation",
                    )
        toggle.connect("notify::active", toggled); row.add_suffix(toggle)
        now = self.action_button(presentation.action_label, "emblem-synchronizing-symbolic")
        now.set_sensitive(presentation.action_enabled)
        now.set_tooltip_text(
            presentation.action_label if presentation.action_enabled else presentation.disabled_reason
        )
        def one(_button, sid=source.source_id, label=display_label):
            if not sync_action_scope_current(self, scope):
                return
            def completed(_result):
                if not sync_action_scope_current(self, scope):
                    return
                self._refresh_sync_page()
                # Refresh replaces preview_generation by design. Preserve
                # Library refresh only if identity and page owner are intact.
                refreshed = capture_sync_action_scope(self, page_token=page_token)
                if refreshed is not None and refreshed.identity_id == scope.identity_id:
                    self.refresh_library(False)
            self.runner.submit(
                f"Synchronisation {label}…",
                lambda: broker.synchronize_source(sid)
                if sync_action_scope_current(self, scope) else None,
                completed, control=_button,
                priority=TaskPriority.USER_ACTION, kind="source-sync",
            )
        now.connect("clicked", one); row.add_suffix(now)
        if source.provider_id == "kobo":
            # Only a targeted Core broker may resolve the private credential.
            # Neither page building nor switching identity reads the ledger.
            preflight = self.action_button(
                "Aperçu local…", "document-properties-symbolic",
            )
            capability = "local_materialization" in source.sync_capabilities
            supported = callable(getattr(broker, "preview_local_materialization", None))
            preflight.set_sensitive(bool(source.enabled and capability and supported))
            preflight.set_tooltip_text(
                "Prévisualiser ce seul compte, hors réseau, sans téléchargement"
                if capability and supported else
                "Aperçu Kobo non pris en charge par cette source"
            )
            row.add_suffix(preflight)
            preview_row = Adw.ActionRow()
            preview_row.set_title("Aperçu local des acquisitions Kobo")
            preview_row.set_subtitle(
                "Non consulté · pas de téléchargement ni de rafraîchissement distant"
            )
            preview_row.set_subtitle_lines(0)
            def preview_one(
                button, sid=source.source_id, identity_id=source.identity_id,
                version=preview_generation, output=preview_row,
            ):
                current = getattr(self.service.current_identity, "identity_id", None)
                if (
                    not sync_action_scope_current(self, scope)
                    or current != identity_id
                ):
                    return
                output.set_subtitle("Calcul local en cours…")
                def still_current():
                    return (
                        not getattr(self, "_closed", False)
                        and getattr(self, "_sync_preview_generation", None) == version
                        and getattr(self.service.current_identity, "identity_id", None) == identity_id
                        and sync_action_scope_current(self, scope)
                    )
                def completed(result):
                    if still_current():
                        output.set_subtitle(sync_local_preflight_presentation(result))
                def failed(_error):
                    if still_current():
                        # Never display an exception that may contain private
                        # paths, provider errors, credentials or bearer URLs.
                        output.set_subtitle(
                            "Aperçu local indisponible. Aucun téléchargement autorisé."
                        )
                self.runner.submit(
                    "Prévisualisation locale Kobo…",
                    lambda: broker.preview_local_materialization(sid)
                    if sync_action_scope_current(self, scope) else None,
                    completed, failed, control=button,
                    priority=TaskPriority.USER_ACTION, kind="sync-preflight",
                )
            preflight.connect("clicked", preview_one)
            group.add(row)
            group.add(preview_row)

            # #1345/#1347: explicit, offline, identity-scoped SHA audit.
            # Unlike a backup or source refresh, it does not require a live
            # Kobo session, online access or enabled acquisition settings.
            # The Core broker resolves the credential, never GTK.
            audit_supported = callable(
                getattr(broker, "audit_local_reconciliation", None)
            )
            audit_button = self.action_button(
                "Vérifier les fichiers…", "document-properties-symbolic",
            )
            audit_button.set_sensitive(audit_supported)
            audit_button.set_tooltip_text(
                "Audit SHA local de ce compte (lecture seule, peut être long)"
                if audit_supported else "Audit local non pris en charge"
            )
            row.add_suffix(audit_button)
            audit_row = Adw.ActionRow()
            audit_row.set_title("Vérification des fichiers et rapprochements")
            audit_row.set_subtitle(
                "Non consulté · aucun téléchargement, déplacement ou suppression"
            )
            audit_row.set_subtitle_lines(0)

            def audit_one(
                button, sid=source.source_id, identity_id=source.identity_id,
                version=preview_generation, output=audit_row,
            ):
                if (
                    not sync_action_scope_current(self, scope)
                    or getattr(self.service.current_identity, "identity_id", None)
                       != identity_id
                ):
                    return
                output.set_subtitle("Inspection locale en cours (lecture seule)…")
                def still_current():
                    return (
                        not getattr(self, "_closed", False)
                        and getattr(self, "_sync_preview_generation", None) == version
                        and getattr(self.service.current_identity, "identity_id", None)
                            == identity_id
                        and sync_action_scope_current(self, scope)
                    )
                def completed(result):
                    if still_current():
                        output.set_subtitle(
                            sync_local_reconciliation_presentation(result)
                        )
                def failed(_error):
                    if still_current():
                        # Errors may carry absolute paths, rights and secrets.
                        output.set_subtitle(
                            "Audit local indisponible. Aucun fichier modifié."
                        )
                self.runner.submit(
                    "Audit local Kobo…",
                    lambda: broker.audit_local_reconciliation(sid)
                    if sync_action_scope_current(self, scope) else None,
                    completed, failed, control=button,
                    priority=TaskPriority.USER_ACTION, kind="sync-audit",
                )
            audit_button.connect("clicked", audit_one)
            group.add(audit_row)

            # #1347: this is the *only* GUI entry for confirmed Kobo local
            # acquisitions. Unlike the advisory local preview above, the
            # broker prepares a fresh account-bound manifest and one-use token.
            # No preparation or materialization runs while building this page.
            download_button = self.action_button(
                "Télécharger les livres manquants…", "folder-download-symbolic",
            )
            can_download = (
                source.enabled and source.connected and capability
                and callable(getattr(broker, "prepare_local_materialization", None))
                and callable(getattr(broker, "materialize_source", None))
            )
            download_button.set_sensitive(bool(can_download))
            download_button.set_tooltip_text(
                "Préparer puis confirmer le téléchargement de ce compte uniquement"
                if can_download else
                "Compte Kobo connecté et capable de téléchargement requis"
            )
            row.add_suffix(download_button)
            stop_download = self.action_button("Annuler le téléchargement", "process-stop-symbolic")
            stop_download.set_sensitive(False)
            row.add_suffix(stop_download)
            acquisition_row = Adw.ActionRow()
            acquisition_row.set_title("Téléchargement local · confirmation obligatoire")
            acquisition_row.set_subtitle(
                "Non préparé · aucun fichier téléchargé. Vérifiez d’abord les droits et fichiers."
            )
            acquisition_row.set_subtitle_lines(0)
            group.add(acquisition_row)
            acquisition_state = {"serial": 0, "cancel": None}

            def download_current():
                return (
                    sync_action_scope_current(self, scope)
                    and getattr(self.service.current_identity, "identity_id", None)
                        == source.identity_id
                )

            def cancel_download(_button, state=acquisition_state, output=acquisition_row):
                active = state["cancel"]
                if active is not None:
                    active.set()
                    _button.set_sensitive(False)
                    if download_current():
                        output.set_subtitle(
                            "Annulation demandée. Les fichiers déjà terminés sont conservés."
                        )
            stop_download.connect("clicked", cancel_download)

            def prepare_download(
                button, sid=source.source_id, identity_id=source.identity_id,
                account_label=display_label, output=acquisition_row,
                stop=stop_download, state=acquisition_state,
            ):
                if not download_current() or not can_download:
                    return
                state["serial"] += 1
                serial = state["serial"]
                state["cancel"] = None
                stop.set_sensitive(False)
                output.set_subtitle(
                    "Préparation distante des droits et vérification locale… Aucun téléchargement."
                )

                def prepared(result):
                    if not download_current() or state["serial"] != serial:
                        return
                    facts = getattr(result, "facts", None)
                    if (
                        getattr(result, "ok", None) is not True
                        or not isinstance(facts, dict)
                        or facts.get("status") != "prepared"
                        or facts.get("requires_explicit_confirmation") is not True
                        or facts.get("acquisition_authorized") is not False
                        or facts.get("manifest_refreshed") is not True
                    ):
                        output.set_subtitle(
                            "Préparation refusée : droits, compte ou rapprochements à vérifier. "
                            "Aucun téléchargement."
                        )
                        return
                    token = facts.get("confirmation_token")
                    expires = facts.get("expires_in_seconds")
                    counts = facts.get("counts")
                    required = (
                        "already_local_linked", "already_local_verified",
                        "needs_reconciliation", "candidate_unverified",
                        "potentially_missing", "attention_required",
                        "blocked_scope", "blocked_unlinked", "not_downloadable",
                        "invalid_manifest_items",
                    )
                    if (
                        type(token) is not str or len(token) != 64
                        or any(char not in "0123456789abcdef" for char in token)
                        or type(expires) is not int or not 0 < expires <= 600
                        or not isinstance(counts, dict)
                        or any(type(counts.get(key)) is not int
                               or not 0 <= counts[key] <= 1000000 for key in required)
                    ):
                        output.set_subtitle(
                            "Réponse de préparation invalide. Aucun téléchargement."
                        )
                        return
                    linked = counts["already_local_linked"] + counts["already_local_verified"]
                    missing = counts["potentially_missing"]
                    uncertain = counts["needs_reconciliation"] + counts["candidate_unverified"]
                    blocked = sum(counts[key] for key in (
                        "attention_required", "blocked_scope", "blocked_unlinked",
                        "not_downloadable", "invalid_manifest_items",
                    ))
                    summary = (
                        f"Compte : {account_label} · déjà locaux : {linked} · "
                        f"manquants possibles : {missing} · à rapprocher : {uncertain} · "
                        f"non admissibles : {blocked}."
                    )
                    if missing == 0:
                        output.set_subtitle(summary + " Aucun fichier à télécharger.")
                        return
                    output.set_subtitle(summary + " En attente d’une confirmation explicite.")
                    dialog = Adw.MessageDialog.new(
                        self, "Confirmer les téléchargements Kobo ?",
                        summary + "\n\nSeuls les fichiers admissibles de ce compte "
                        "seront traités. Le manifeste et les preuves locales "
                        "seront revérifiés sous verrou. Les fichiers déjà "
                        "acquis sont conservés en cas d'annulation."
                    )
                    dialog.add_response("cancel", "Annuler")
                    dialog.add_response("run", "Télécharger pour ce compte")
                    dialog.set_response_appearance(
                        "run", Adw.ResponseAppearance.SUGGESTED,
                    )

                    def response(_dialog, choice):
                        if choice != "run" or not download_current() or state["serial"] != serial:
                            return
                        # The token remains in this closure only: no logs,
                        # widget property or persisted setting may hold it.
                        from threading import Event
                        cancellation = Event()
                        state["cancel"] = cancellation
                        stop.set_sensitive(True)
                        output.set_subtitle(
                            "Téléchargement de ce compte en cours · nombre total inconnu…"
                        )

                        def work(report):
                            # No widget access from the worker. Scope is a
                            # GTK-free identity/page generation predicate.
                            if cancellation.is_set() or not download_current():
                                return None

                            class ScopedControl:
                                def cancelled(self):
                                    return cancellation.is_set() or not download_current()

                                def check_cancelled(self):
                                    if self.cancelled():
                                        raise RuntimeError("Acquisition annulée")

                                def report(self, *, current=None, total=None, message=""):
                                    if self.cancelled():
                                        return
                                    if type(current) is int and 0 <= current <= 1000000:
                                        report(f"Téléchargement Kobo · {current} fichier(s) acquis")
                                    else:
                                        report("Téléchargement Kobo en cours…")

                            try:
                                return broker.materialize_source(
                                    sid, token, control=ScopedControl(),
                                )
                            except Exception:
                                # TaskRunner records exceptions before its
                                # callback; redact at the worker boundary.
                                raise RuntimeError("Téléchargement Kobo indisponible") from None

                        def done(result):
                            if not download_current() or state["serial"] != serial:
                                return
                            stop.set_sensitive(False)
                            state["cancel"] = None
                            if result is None:
                                output.set_subtitle(
                                    "Opération interrompue : aucune nouvelle acquisition lancée."
                                )
                                return
                            facts_out = getattr(result, "facts", None)
                            facts_out = facts_out if isinstance(facts_out, dict) else {}
                            status = facts_out.get("status")
                            changed = getattr(result, "changed", None)
                            if type(changed) is not int or not 0 <= changed <= 1000000:
                                changed = None
                            count_label = (
                                f"{changed} fichier(s) acquis durablement" if changed is not None
                                else "Nombre de fichiers acquis indéterminé"
                            )
                            if getattr(result, "ok", None) is True and status == "success":
                                output.set_subtitle("Téléchargement terminé · " + count_label + ".")
                            else:
                                known_status = {
                                    "cancelled": "Annulé",
                                    "partial": "Partiel",
                                    "blocked": "Bloqué",
                                    "acquisition_busy": "Verrou occupé",
                                    "manifest_changed": "Manifeste modifié",
                                    "preview_changed": "État local modifié",
                                }.get(status if type(status) is str else None, "Non terminé")
                                output.set_subtitle(
                                    known_status + " · " + count_label
                                    + ". Relancer une préparation avant toute reprise."
                                )
                            if facts_out.get("completion_unknown") is True:
                                output.set_subtitle(
                                    "Résultat incomplet : vérifier les fichiers avant toute reprise."
                                )
                            if changed and download_current():
                                self.refresh_library(False)

                        def failed(_exc):
                            if download_current() and state["serial"] == serial:
                                stop.set_sensitive(False)
                                state["cancel"] = None
                                output.set_subtitle(
                                    "Téléchargement indisponible ; vérifier les fichiers "
                                    "avant de réessayer. Aucun rollback des fichiers acquis."
                                )

                        self.runner.submit(
                            "Téléchargement Kobo confirmé…",
                            work, done, failed, control=button,
                            priority=TaskPriority.USER_ACTION,
                            kind="kobo-acquisition", with_progress=True,
                            origin="user",
                        )
                    dialog.connect("response", response)
                    dialog.present()

                def failed(_error):
                    if download_current() and state["serial"] == serial:
                        output.set_subtitle(
                            "Préparation indisponible. Aucun téléchargement lancé."
                        )

                self.runner.submit(
                    "Préparer les acquisitions Kobo…",
                    lambda: broker.prepare_local_materialization(sid)
                    if download_current() else None,
                    prepared, failed, control=button,
                    priority=TaskPriority.USER_ACTION,
                    kind="sync-preflight", origin="user",
                )
            download_button.connect("clicked", prepare_download)
        else:
            group.add(row)


def run_opening_sync(self, control: Gtk.Widget | None = None, *, deep: bool = False) -> None:
    # Connection completion schedules this via GLib.idle_add. The window
    # might have closed after scheduling but before this callback runs.
    if getattr(self, "_closed", False):
        return

    def progress(event: dict[str, Any], report) -> None:
        if getattr(self, "_closed", False):
            return
        kind = str(event.get("event") or "")
        if kind == "identity_begin":
            # Identity IDs are private internal routing keys. If the service
            # has no user-facing name, do not fall back to the raw ID.
            identity_label = event.get("label")
            identity_label = identity_label.strip() if type(identity_label) is str else ""
            label = f"Synchronisation · {identity_label or 'une identité'}…"
            report(label)
    def work(report):
        try:
            return self.service.opening_sync(
                progress=lambda event: progress(event, report), deep=deep,
            )
        except Exception:
            # TaskRunner also renders failed worker payloads in global GTK
            # feedback *before* invoking err(). Never enqueue a raw provider
            # exception with a bearer URL/path/account ID into that boundary.
            raise RuntimeError("Synchronisation des bibliothèques indisponible") from None

    def ok(summary: dict[str, Any]) -> None:
        if getattr(self, "_closed", False):
            return
        self._render_dashboard(); self._populate_loans(self.service.last_loans); self.refresh_library(False); self.refresh_recent()
        acquisitions = sum(int(x.get("new_acquisitions") or 0) for x in summary.get("identities", []))
        attention = sum(int(x.get("attention_required") or 0) for x in summary.get("identities", []))
        errors = sum(int(x.get("errors") or 0) for x in summary.get("identities", []))
        if acquisitions or attention or errors:
            self._activity_result(
                f"Synchronisation : {acquisitions} acquisition(s), {attention} action(s), {errors} erreur(s)",
                success=not bool(attention or errors),
                persistent=bool(attention or errors),
            )
    def err(exc: BaseException) -> None:
        if getattr(self, "_closed", False):
            return
        self.diagnostics.add("SYNC", "opening sync", "error", detail={"reason": "opening_sync_failed"})
        self._render_dashboard()
        if control is not None:
            self.show_error("Synchronisation des bibliothèques indisponible.", "Synchronisation impossible")
    task_label = "Vérification complète de la bibliothèque…" if deep else "Synchronisation des bibliothèques…"
    self.runner.submit(
        task_label, work, ok, err, control=control,
        priority=TaskPriority.USER_ACTION if control is not None else (TaskPriority.MAINTENANCE if deep else TaskPriority.ACTIVE_LOAN),
        kind="deep-sync" if deep else "opening-sync",
        with_progress=True,
        # Opening sync covers *all configured identities*: its automatic
        # shallow requests have one scope. Never coalesce a physical refresh
        # button, a deep verification, or a distinct source sync.
        coalesce_key="opening:global:shallow" if control is None and not deep else None,
        origin="user" if control is not None else "maintenance" if deep else "automatic",
    )

# Reader/storage targets are provided by storage.target plugins.
# The first 0.9.2 prototype intentionally has no brand-specific reader UI.


def _configure_kobo_backup_timer(self, *, run_now: bool = False) -> None:
    """0.8.2 compatibility shim: automation runs at application opening only.

    The historical interval setting remains readable for rollback compatibility,
    but Paris no longer installs a recurring in-process timer.
    """
    if getattr(self, "_closed", False):
        return
    if self._kobo_backup_timer_id is not None:
        try:
            GLib.source_remove(self._kobo_backup_timer_id)
        except Exception:
            pass
        self._kobo_backup_timer_id = None
    if run_now and self.service.kobo_backup_enabled():
        GLib.idle_add(lambda: (
            self.run_kobo_backup(False) if not getattr(self, "_closed", False) else None,
            False,
        )[1])


def run_kobo_backup(self, manual: bool = True, control: Gtk.Widget | None = None) -> None:
    if getattr(self, "_closed", False):
        return
    original_label = control.get_label() if isinstance(control, Gtk.Button) else None
    progress_state = {"last": 0}

    def progress(event: dict[str, Any]) -> None:
        if getattr(self, "_closed", False) or not isinstance(control, Gtk.Button):
            return
        kind = str(event.get("event") or "")
        current = int(event.get("current") or 0)
        total = int(event.get("total") or 0)
        if kind == "adept_probe":
            label = "Synchronisation Kobo · vérification Adept…"
        elif kind in {"manifest_fetch", "manifest"}:
            label = f"Synchronisation Kobo · manifest {total}" if total else "Synchronisation Kobo · manifest…"
        elif kind in {"scan", "acquiring", "downloaded", "item_error"} and total:
            if kind == "scan" and current not in {1, total} and current - progress_state["last"] < 10:
                return
            progress_state["last"] = current
            label = f"Synchronisation Kobo · {current}/{total}"
        else:
            return
        def update_label() -> bool:
            # Progress from a Core worker can already be enqueued when the
            # owning window (and this button) gets destroyed.
            if not getattr(self, "_closed", False):
                control.set_label(label)
            return False
        GLib.idle_add(update_label)

    def work() -> dict[str, Any]:
        try:
            return self.service.sync_kobo_backup(progress=progress)
        except Exception:
            # The global task feedback path prints worker exceptions. A raw
            # provider failure can contain private delegation URLs or tokens.
            raise RuntimeError("Synchronisation locale Kobo indisponible") from None

    def restore_label() -> None:
        if getattr(self, "_closed", False):
            return
        if isinstance(control, Gtk.Button) and original_label is not None:
            control.set_label(original_label)

    def ok(summary: dict[str, Any]) -> None:
        if getattr(self, "_closed", False):
            return
        restore_label()
        self._render_kobo_loans()
        attention = int(summary.get("attention_required", 0) or 0)
        errors = int(summary.get("errors", 0) or 0)
        pending = int(summary.get("pending", 0) or 0)
        remaining = int(summary.get("remaining", 0) or 0)
        state_labels = {
            "complete": "terminée",
            "partial": "partielle",
            "attention_required": "action requise",
            "aborted": "interrompue",
            "failed": "en échec",
            "cancelled": "annulée",
        }
        state = summary.get("status")
        if type(state) is not str or state not in state_labels:
            state = "unknown"
        # A completed worker task is not proof that Kobo materialization
        # completed. Fail closed if the status or its outstanding counters
        # disagree, including a truncated manifest with no item errors.
        outstanding = bool(
            attention or errors or pending or remaining
            or summary.get("reconcile_pending")
            or summary.get("malformed_manifest_items")
        )
        if state == "complete" and outstanding:
            state = "partial"
        success = state == "complete"
        state_text = state_labels.get(state, "état non confirmé")
        remaining_text = f", {remaining} restant(s)" if remaining > 0 else ""
        if manual:
            self._activity_result(
                f"Synchronisation Kobo ({state_text}) : {summary.get('downloaded', 0)} téléchargé(s), "
                f"{pending} en attente, {summary.get('unchanged', 0)} à jour, "
                f"{summary.get('skipped', 0)} ignoré(s), {attention} action(s), {errors} erreur(s)"
                f"{remaining_text}",
                success=success,
                persistent=not success,
            )
        elif (not success or summary.get("downloaded") or pending or attention or errors):
            self._activity_result(
                f"Synchronisation Kobo automatique ({state_text}) : {summary.get('downloaded', 0)} téléchargé(s), "
                f"{pending} en attente, {attention} action(s), {errors} erreur(s)"
                f"{remaining_text}",
                success=success,
                persistent=not success,
            )
    def err(exc: BaseException) -> None:
        if getattr(self, "_closed", False):
            return
        restore_label()
        if manual:
            self.show_error("Synchronisation locale Kobo indisponible.", "Synchronisation Kobo impossible")
        else:
            self.diagnostics.add("KOBO", "automatic backup task", "error", detail={"reason": "kobo_sync_failed"})
    self.runner.submit(
        "Synchronisation locale Kobo…", work, ok, err, control=control,
        priority=TaskPriority.USER_ACTION if manual else TaskPriority.ACTIVE_LOAN, kind="kobo-acquisition",
    )
