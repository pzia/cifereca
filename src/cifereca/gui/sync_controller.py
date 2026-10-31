"""GTK synchronization page and runner orchestration (#1131).

This module delegates exclusively to existing service/broker contracts, and
uses MainWindow as a host without importing it or starting threads of its own.
"""
from __future__ import annotations

from typing import Any

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Pango

from ..plugin_api import SyncRequest
from .action_widgets import add_css, clear_box
from .sync_view import (
    sync_identity_action_label, sync_source_presentation,
    sync_source_attention, sync_source_row_labels,
    sync_local_preflight_presentation, sync_local_reconciliation_presentation,
    sync_numilog_local_preview_presentation,
)
from .task_runner import TaskPriority
from .sync_action_scope import capture_sync_action_scope, sync_action_scope_current
from .sync_action_layout import add_sync_action, sync_action_row
from .sync_source_details import add_sync_detail


def _build_sync_page(self):
    if getattr(self, "_closed", False):
        return Gtk.Label(label="Synchronisation indisponible")
    page_token = object()
    self._sync_page_token = page_token
    # #2141: keep the account context and global actions visible while only
    # the potentially long list of sources scrolls. Previously the whole page
    # lived inside this ScrolledWindow and lost its identity/status on scroll.
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_vexpand(True)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for edge in ("bottom", "start", "end"):
        getattr(outer, "set_margin_" + edge)(20)
    heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for edge in ("top", "start", "end"):
        getattr(heading, "set_margin_" + edge)(20)
    heading.set_margin_bottom(8)
    title = Gtk.Label(label="Synchronisation", xalign=0); title.set_hexpand(True); add_css(title, "page-title")
    title.set_wrap(True)
    title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    title.set_width_chars(1)
    refresh = self.action_button("Actualiser l’affichage", "view-refresh-symbolic")
    all_now = self.action_button("Synchroniser les sources", "emblem-synchronizing-symbolic", suggested=True)
    self._sync_all_button = all_now
    heading.append(title)
    self._sync_summary = Gtk.Label(label="Sélectionnez une identité", xalign=0)
    self._sync_summary.set_wrap(True)
    heading.append(self._sync_summary)
    # Aggregated, closed-state attention summary is independent from
    # per-account acquisition or inventory consent, and reveals no account ID.
    alert_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    alert_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
    alert_text = Gtk.Label(label="", xalign=0)
    alert_text.set_wrap(True)
    alert_row.append(alert_icon)
    alert_row.append(alert_text)
    alert_row.set_visible(False)
    self._sync_attention_row = alert_row
    self._sync_attention_icon = alert_icon
    self._sync_attention_text = alert_text
    heading.append(alert_row)
    add_sync_action(heading, refresh)
    add_sync_action(heading, all_now)
    root.append(sync_action_row(heading))
    scope_note = Gtk.Label(
        label="Synchroniser actualise les inventaires et peut recevoir les fichiers disponibles "
        "lorsque le téléchargement automatique de ce compte est autorisé. "
        "Chaque compte conserve son propre réglage.",
        xalign=0,
    )
    scope_note.set_wrap(True)
    outer.append(scope_note)
    # Source panels consume the available scroll viewport width instead of
    # inheriting a narrow natural width from their child labels/actions.
    outer.set_hexpand(True)
    self._sync_sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    self._sync_sources_box.set_hexpand(True)
    outer.append(self._sync_sources_box)
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
    scroll.set_child(outer)
    root.append(scroll)
    return root


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
    summary = getattr(self, "_sync_summary", None)
    alert_row = getattr(self, "_sync_attention_row", None)
    if alert_row is not None:
        # Reset before all early returns (no broker, no identity, stale scope).
        alert_row.set_visible(False)
    if summary is not None:
        summary.set_label("État des sources indisponible")
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
        if summary is not None:
            summary.set_label("Sélectionnez une identité")
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
    # #3105: a configured Paris credential with a failed/absent session is
    # NOT a SyncSourceRef. Only a separate, inert configuration row may show it.
    # The helper reads local config only, scoped to this identity.
    from cifereca.core.sync_connection_visibility import unavailable_paris_connections
    unavailable_paris = unavailable_paris_connections(self.service, scope.identity_id)
    if not sync_action_scope_current(self, scope):
        return
    presentations = [sync_source_presentation(source) for source in rows]
    # Keep a user's open account tools across passive refreshes, but never
    # carry that disclosure state into another identity or retain stale rows.
    open_tools = (getattr(self, "_sync_tools_state", {})
                  if getattr(self, "_sync_tools_identity", None) == scope.identity_id
                  else {})
    self._sync_tools_identity = scope.identity_id
    self._sync_tools_state = {
        source.source_id: open_tools.get(source.source_id, False) is True
        for source in rows
    }
    # Number genuinely indistinguishable same-provider connections without
    # exposing any private credential/account/source ID. Unique titles stay
    # unchanged to preserve existing keyboard navigation and accessible names.
    row_titles = sync_source_row_labels(rows)
    actionable = any(item.action_enabled for item in presentations)
    if summary is not None:
        summary.set_label(
            f"{len(rows)} source(s) · "
            f"{sum(item.action_enabled for item in presentations)} synchronisable(s) maintenant"
            + (f" · {len(unavailable_paris)} connexion(s) Paris configurée(s) non disponibles"
               if unavailable_paris else "")
        )
    if alert_row is not None:
        # Never use provider diagnostics or source labels in the global
        # attention count; the bounded fixed-state projection is shared with
        # the individual per-account indicators.
        alerts = [sync_source_attention(source) for source in rows]
        errors = sum(alert is not None and alert[0] == "error" for alert in alerts)
        warnings = sum(alert is not None and alert[0] == "warning" for alert in alerts)
        infos = sum(alert is not None and alert[0] == "info" for alert in alerts)
        if errors or warnings:
            self._sync_attention_icon.set_from_icon_name(
                "dialog-error-symbolic" if errors else "dialog-warning-symbolic"
            )
            extra = f" · {infos} couverture(s) partielle(s) informative(s)" if infos else ""
            self._sync_attention_text.set_label(
                f"À vérifier : {errors} échec(s) · {warnings} avertissement(s)"
                + extra + ". Consultez les comptes signalés ci-dessous."
            )
            alert_row.set_visible(True)
        elif infos:
            self._sync_attention_icon.set_from_icon_name("dialog-information-symbolic")
            self._sync_attention_text.set_label(
                f"Information : {infos} source(s) à couverture partielle ; "
                "aucun échec local déclaré."
            )
            alert_row.set_visible(True)
    if all_now is not None:
        all_now.set_sensitive(actionable)
        all_now.set_tooltip_text(
            global_label if actionable
            else "Aucune source activée, connectée et synchronisable pour cette identité"
        )
    def append_catalogue_searches():
        """Read-only active catalogue inventory: never invent catalogue sync.

        Sources may be singleton_public and have no Account. The Core's
        declared capability snapshot is metadata-only and identity-scoped.
        No switch, network request, sync job or download is created here.
        """
        reader = getattr(self.service, "catalogue_source_capabilities", None)
        if not callable(reader):
            return
        try:
            raw = reader(identity_id=scope.identity_id)
        except Exception:
            # A plugin/provider diagnostic can contain signed URLs, so do
            # not echo it. Personal-library sync cards remain available.
            return
        if not sync_action_scope_current(self, scope):
            return
        if type(raw) not in (list, tuple) or len(raw) > 128:
            return
        catalogues = []
        for item in raw:
            provider = getattr(item, "provider_id", None)
            if (type(provider) is not str or not 1 <= len(provider) <= 64
                    or not all(char.isascii() and
                               (char.isalnum() or char in "._-")
                               for char in provider)
                    or getattr(item, "catalog_search_supported", None) is not True
                    or getattr(item, "catalog_refresh", None) != "unsupported"
                    or getattr(item, "identity_scope", None) not in
                        {"public", "authenticated"}):
                continue
            catalogues.append(item)
        if not catalogues:
            return
        # A disclosure keeps the #2141 dense Synchronisation page usable.
        # These are catalogue SEARCH capabilities, not synchronizable cards.
        disclosure = Gtk.Expander(label=f"Catalogues · recherche ({len(catalogues)})")
        # A disconnected configured catalogue must remain discoverable without
        # pretending that a remote search or catalogue sync is available.
        disclosure.set_expanded(any(
            item.identity_scope == "authenticated"
            and getattr(item, "catalog_search_available", None) is not True
            for item in catalogues
        ))
        group = Adw.PreferencesGroup()
        group.set_title("Recherche de catalogues — aucune actualisation d’index")
        for item in catalogues:
            row = Adw.ActionRow()
            # Only registered descriptor IDs, never credentials, can label
            # a catalogue in this read-only section.
            # Names are descriptor metadata, not supplier-specific policies.
            # A missing/invalid display name falls back to the bounded ID
            # already validated above; never render provider diagnostics.
            name_reader = getattr(self.service, "provider_display_name", None)
            try:
                display_name = name_reader(item.provider_id) if callable(name_reader) else None
            except Exception:
                display_name = None
            if (type(display_name) is not str or not 1 <= len(display_name) <= 80
                    or not display_name.isprintable()
                    or any(char in display_name for char in "/\\?@")):
                display_name = item.provider_id
            row.set_title(display_name)
            availability = (
                "Recherche disponible"
                if getattr(item, "catalog_search_available", None) is True
                else "Recherche indisponible (source non connectée)"
            )
            scope_label = (
                "Catalogue public"
                if item.identity_scope == "public"
                else "Catalogue avec compte"
            )
            row.set_subtitle(
                f"{scope_label} · {availability}\n"
                "Synchronisation de catalogue non prise en charge ; "
                "aucun téléchargement."
            )
            group.add(row)
        disclosure.set_child(group)
        box.append(disclosure)

    def append_unavailable_paris():
        # Disabled cards cannot be routed through broker.synchronize_source,
        # toggled as SyncSourceRef, or used as proof of an active loan.
        descriptions = {
            "not_connected": "Compte configuré · connexion non établie dans cette session.",
            "account_disabled": "Compte désactivé dans la configuration de ce profil.",
            "plugin_missing": "Plugin Paris absent ou non chargé.",
            "plugin_unavailable": "Plugin Paris non prêt.",
            "plugin_disabled": "Plugin Paris désactivé pour ce profil.",
            "invalid_configuration": "Configuration du compte à vérifier.",
        }
        for index, notice in enumerate(unavailable_paris, start=1):
            if not sync_action_scope_current(self, scope):
                return
            group = Adw.PreferencesGroup()
            group.set_hexpand(True)
            add_css(group, "sync-source-panel")
            row = Adw.ActionRow()
            row.set_title(
                "Bibliothèques de Paris · Connexion configurée"
                + (f" ({index})" if len(unavailable_paris) > 1 else "")
            )
            row.set_subtitle(
                descriptions[notice.state]
                + "\nPrêts : synchronisation impossible sans compte connecté et vérifié."
            )
            row.set_subtitle_lines(0)
            icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            icon.set_valign(Gtk.Align.CENTER)
            row.add_prefix(icon)
            if notice.can_reconnect and callable(getattr(self, "connect_configured_sources", None)):
                reconnect = self.action_button(
                    "Reconnecter toutes les sources", "view-refresh-symbolic",
                )
                reconnect.set_tooltip_text(
                    "Action volontaire : relance les connexions et leur synchronisation d’ouverture"
                )
                def retry(button):
                    if not sync_action_scope_current(self, scope):
                        return
                    button.set_sensitive(False)
                    self.connect_configured_sources()
                reconnect.connect("clicked", retry)
                row.add_suffix(reconnect)
            group.add(row)
            box.append(group)

    if not rows:
        if not unavailable_paris:
            box.append(Gtk.Label(label="Aucune source synchronisable pour cette identité", xalign=0))
        append_unavailable_paris()
        append_catalogue_searches()
        return
    append_unavailable_paris()
    # Prioritize actual sync-capable accounts. Public-only catalogues remain
    # reachable and activatable, but must not push Kobo/Numilog below the fold.
    ordered_sources = sorted(
        zip(rows, presentations, row_titles),
        key=lambda item: (not bool(item[0].sync_capable), not item[1].action_enabled),
    )
    for source, presentation, display_label in ordered_sources:
        if not sync_action_scope_current(self, scope):
            return
        # Refuse a broker row whose ownership does not match the explicitly
        # selected profile. No toggle/preview may be wired to that row.
        if getattr(source, "identity_id", None) != scope.identity_id:
            continue
        source_consent_api = all(callable(getattr(broker, name, None)) for name in (
            "source_sync_consent", "set_source_sync_consent",
        ))
        if source_consent_api:
            presentation = sync_source_presentation(source, canonical=True)
        # The scoped broker order determines ephemeral labels only; sid remains
        # the opaque, separate source route for each row.
        group = Adw.PreferencesGroup()
        # A distinct visual boundary must survive a long expanded tools list:
        # group and actions are scoped to exactly this account.
        group.set_hexpand(True)
        add_css(group, "sync-source-panel")
        box.append(group)
        group._cifereca_sync_tools_state = self._sync_tools_state
        group._cifereca_sync_source_id = source.source_id
        row = Adw.ActionRow(); row.set_title(display_label)
        add_css(row, "sync-source-heading")
        if source.sync_capable:
            # Errors cannot disappear in low-contrast multi-line subtitles or
            # behind the per-account tools expander. An icon and a short fixed
            # first line stay at the source header without provider secrets.
            alert = sync_source_attention(source)
            if alert is not None:
                severity, message = alert
                icon = Gtk.Image.new_from_icon_name(
                    "dialog-error-symbolic" if severity == "error"
                    else "dialog-information-symbolic" if severity == "info"
                    else "dialog-warning-symbolic"
                )
                icon.set_valign(Gtk.Align.CENTER)
                icon.set_tooltip_text(message)
                add_css(icon, severity)
                row.add_prefix(icon)
            # Keep the full phase/account evidence below the prominent line.
            row.set_subtitle(
                (alert[1] + "\n" if alert is not None else "")
                + presentation.summary
            )
            row.set_subtitle_lines(0)
        else:
            # An account row without sync capability is NOT evidence of a
            # public catalogue, much less an exhaustively refreshable one.
            # Keep a neutral explanation and the independent activation flag.
            row.set_subtitle(
                "Recherche de catalogue distincte, si disponible · "
                + presentation.activation_label
                + " · " + presentation.connection_label
                + "\nInventaire synchronisé non pris en charge"
            )
            row.set_subtitle_lines(2)
        toggle = Gtk.Switch(); toggle.set_active(source.enabled); toggle.set_valign(Gtk.Align.CENTER)
        # Account availability and automatic file consent are independent.
        # Never imply that enabling a source authorizes a new acquisition.
        toggle.set_tooltip_text(
            "Activer cette source pour la recherche éventuelle ; "
            "n’autorise aucun téléchargement automatique."
            if not source.sync_capable else
            "Activer la source et ses synchronisations ; "
            "les téléchargements automatiques exigent une autorisation distincte."
        )
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
        now.connect("clicked", one)
        now.set_valign(Gtk.Align.CENTER)
        if source.sync_capable:
            add_sync_action(row, now)
            if source_consent_api:
                from cifereca.gui.source_sync_consent_controls import (
                    append_source_sync_consent_controls,
                )
                group.add(sync_action_row(row))
                append_source_sync_consent_controls(self, broker, source, scope, group)
                declared = getattr(source, "sync_capabilities", ())
                if (type(declared) in (tuple, list)
                        and "remote_inventory" in declared
                        and "scheduled_refresh" in declared):
                    from cifereca.gui.source_inventory_schedule_controls import (
                        append_source_inventory_schedule_controls,
                    )
                    append_source_inventory_schedule_controls(
                        self, broker, source, scope, group,
                    )
                # The Core broker owns both independent decisions: remote
                # notices/rights scheduling and automatic file acquisition.
                # Legacy per-vendor widgets must not re-enter this pathway.
                # even if the generic read fails or the adapter is unsupported.
                continue
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
            preflight.set_valign(Gtk.Align.CENTER)
            add_sync_action(row, preflight)
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
            group.add(sync_action_row(row))
            add_sync_detail(group, preview_row, action=preflight, source_row=row)

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
            audit_button.set_valign(Gtk.Align.CENTER)
            add_sync_action(row, audit_button)
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
            add_sync_detail(group, audit_row, action=audit_button, source_row=row)

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
            stop_download = self.action_button("Annuler le téléchargement", "process-stop-symbolic")
            stop_download.set_sensitive(False)
            # Do not add these actions to the existing source row: its
            # refresh, preview and audit buttons already consume horizontal
            # space. At 1366px GTK otherwise squeezes the source title into
            # a one-character column and expands the row to several thousand
            # pixels, putting even unrelated audit actions off-screen.
            acquisition_row = Adw.ActionRow()
            acquisition_row.set_title("Téléchargement local · confirmation obligatoire")
            acquisition_row.set_subtitle(
                "Non préparé · aucun fichier téléchargé. Vérifiez d’abord les droits et fichiers."
            )
            acquisition_row.set_subtitle_lines(0)
            download_button.set_valign(Gtk.Align.CENTER)
            add_sync_action(acquisition_row, download_button)
            stop_download.set_valign(Gtk.Align.CENTER)
            add_sync_action(acquisition_row, stop_download)
            add_sync_detail(group, acquisition_row)
            acquisition_state = {"serial": 0, "cancel": None}

            def download_current(expected_identity=source.identity_id, captured_scope=scope):
                return (
                    sync_action_scope_current(self, captured_scope)
                    and getattr(self.service.current_identity, "identity_id", None)
                        == expected_identity
                )

            def cancel_download(_button, state=acquisition_state, output=acquisition_row,
                                current=download_current):
                active = state["cancel"]
                if active is not None:
                    active.set()
                    _button.set_sensitive(False)
                    if current():
                        output.set_subtitle(
                            "Annulation demandée. Les fichiers déjà terminés sont conservés."
                        )
            stop_download.connect("clicked", cancel_download)

            def prepare_download(
                button, sid=source.source_id, identity_id=source.identity_id,
                account_label=display_label, output=acquisition_row,
                stop=stop_download, state=acquisition_state,
                current=download_current, allowed=bool(can_download),
            ):
                if not current() or not allowed or state["cancel"] is not None:
                    return
                state["serial"] += 1
                serial = state["serial"]
                state["cancel"] = None
                stop.set_sensitive(False)
                output.set_subtitle(
                    "Préparation distante des droits et vérification locale… Aucun téléchargement."
                )

                def prepared(result):
                    if not current() or state["serial"] != serial:
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
                        if choice != "run" or not current() or state["serial"] != serial:
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
                            if cancellation.is_set() or not current():
                                return None

                            class ScopedControl:
                                def cancelled(self):
                                    return cancellation.is_set() or not current()

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
                            if not current() or state["serial"] != serial:
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
                            if changed and current():
                                self.refresh_library(False)

                        def failed(_exc):
                            if current() and state["serial"] == serial:
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
                    if current() and state["serial"] == serial:
                        output.set_subtitle(
                            "Préparation indisponible. Aucun téléchargement lancé."
                        )

                def prepare_work():
                    if not current():
                        return None
                    try:
                        return broker.prepare_local_materialization(sid)
                    except Exception:
                        # Do not leak a provider exception via TaskRunner.
                        raise RuntimeError("Préparation Kobo indisponible") from None

                self.runner.submit(
                    "Préparer les acquisitions Kobo…",
                    prepare_work,
                    prepared, failed, control=button,
                    priority=TaskPriority.USER_ACTION,
                    kind="sync-preflight", origin="user",
                )
            download_button.connect("clicked", prepare_download)

            # #1346/#1347: optional source-specific *startup* materialization.
            # This is an independent preference, never the old global Kobo
            # toggle. Reading it neither contacts the provider nor prepares a
            # batch; only a dedicated human click can change it.
            read_schedule = getattr(broker, "local_materialization_schedule", None)
            write_schedule = getattr(broker, "set_local_materialization_schedule", None)
            schedule_mode = "unavailable"
            if callable(read_schedule) and callable(write_schedule):
                try:
                    raw_schedule = read_schedule(source.source_id)
                    if type(raw_schedule) is str and raw_schedule in {
                        "manual", "on_start", "not_supported",
                    }:
                        schedule_mode = raw_schedule
                except Exception:
                    # A plugin might raise an exception containing private
                    # account names, URLs or tokens. Never echo that exception.
                    pass
            schedule_row = Adw.ActionRow()
            schedule_row.set_title("Téléchargement automatique de ce compte")
            schedule_row.set_subtitle_lines(0)
            if schedule_mode == "on_start":
                schedule_row.set_subtitle(
                    "Autorisé à l’ouverture pour ce compte uniquement ; "
                    "pas de minuterie périodique. Les contrôles anti-doublons "
                    "et les blocages s’appliquent toujours."
                )
            elif schedule_mode == "manual":
                schedule_row.set_subtitle(
                    "Mode manuel (par défaut) : aucun téléchargement automatique. "
                    "L’actualisation des notices et droits reste indépendante."
                )
            else:
                schedule_row.set_subtitle(
                    "Planification indisponible pour ce compte ; "
                    "aucun téléchargement automatique autorisé depuis cette page."
                )
            schedule_button = self.action_button(
                "Désactiver au démarrage" if schedule_mode == "on_start"
                else "Autoriser au démarrage…",
                "preferences-system-symbolic",
            )
            schedule_button.set_sensitive(bool(
                callable(read_schedule) and callable(write_schedule)
                and (
                    schedule_mode == "on_start"  # disabling is always possible
                    or (
                        schedule_mode == "manual" and source.enabled
                        and source.connected and capability
                    )
                )
            ))
            schedule_button.set_tooltip_text(
                "Ne modifie que la planification de ce compte Kobo. "
                "Aucun téléchargement n’est lancé par ce bouton."
            )
            schedule_button.set_valign(Gtk.Align.CENTER)
            add_sync_action(schedule_row, schedule_button)
            add_sync_detail(group, schedule_row)

            def schedule_change(
                button, sid=source.source_id, account_label=display_label,
                mode=schedule_mode, current=download_current,
                output=schedule_row,
            ):
                if not current() or mode not in {"manual", "on_start"}:
                    return
                target = "manual" if mode == "on_start" else "on_start"

                def apply_mode():
                    # A dialog may have remained open across another edit.
                    # Check both the scoped UI generation and the persisted
                    # source value immediately before the attempted mutation.
                    if not current():
                        return
                    try:
                        if read_schedule(sid) != mode:
                            if current():
                                self._refresh_sync_page()
                            return
                        updated = write_schedule(sid, target)
                        if updated != target:
                            raise ValueError("invalid schedule response")
                    except Exception:
                        if current():
                            self.show_error(
                                "Planification de ce compte Kobo indisponible.",
                                "Synchronisation",
                            )
                        return
                    if current():
                        self._refresh_sync_page()

                if target == "manual":
                    # A source can be disabled even when disconnected. No
                    # dialog is needed to revoke an automatic authorization.
                    apply_mode()
                    return

                dialog = Adw.MessageDialog.new(
                    self, "Autoriser les téléchargements au démarrage ?",
                    f"Compte : {account_label}.\n\n"
                    "Cette autorisation persistante concerne uniquement ce "
                    "compte Kobo. À une prochaine ouverture, l’application "
                    "pourra acquérir automatiquement les fichiers encore "
                    "manquants et admissibles, sans nouvelle confirmation par "
                    "livre. Le volume peut être important et des erreurs "
                    "ADEPT ou des rapprochements incertains peuvent bloquer "
                    "des acquisitions. Aucun fichier n’est téléchargé maintenant.\n\n"
                    "Pour conserver les acquisitions manuelles seulement, "
                    "choisissez Annuler.",
                )
                dialog.add_response("cancel", "Annuler")
                dialog.add_response("confirm", "Autoriser pour ce compte")
                dialog.set_response_appearance(
                    "confirm", Adw.ResponseAppearance.SUGGESTED,
                )

                def response(_dialog, choice):
                    if choice == "confirm" and current():
                        apply_mode()

                dialog.connect("response", response)
                dialog.present()

            schedule_button.connect("clicked", schedule_change)

        else:
            group.add(sync_action_row(row))

        if source.provider_id == 'bnf-pret' and source.sync_capable:
            auto_row = Adw.ActionRow()
            auto_row.set_title('Télécharger automatiquement les prêts BnF')
            auto_row.set_subtitle(
                'À la synchronisation et au démarrage : récupérer les ACSM des prêts actifs '
                'puis produire les EPUB/PDF via le profil ADEPT de cette identité.'
            )
            auto_toggle = Gtk.Switch()
            auto_toggle.set_valign(Gtk.Align.CENTER)
            auto_toggle.set_active(broker.bnf_auto_download_enabled(source.source_id))
            auto_toggle.set_sensitive(bool(source.enabled and source.connected))
            def changed_bnf(widget, _param, sid=source.source_id):
                if not sync_action_scope_current(self, scope):
                    return
                try:
                    broker.set_bnf_auto_download(sid, widget.get_active())
                except Exception:
                    widget.set_active(False)
                    self.show_error('Activation du téléchargement BnF indisponible.', 'Synchronisation')
            auto_toggle.connect('notify::active', changed_bnf)
            auto_row.add_suffix(auto_toggle)
            group.add(auto_row)

        if source.provider_id == "numilog":
            # Offline evidence only, including disconnected accounts. Neither
            # rendering nor a preview grants acquisition/scheduler consent.
            local_row = Adw.ActionRow()
            local_row.set_title("Aperçu local des achats Numilog")
            local_row.set_subtitle("Non consulté · aucun téléchargement ni actualisation distante")
            local_row.set_subtitle_lines(0)
            supported = callable(getattr(broker, "preview_local_materialization", None))
            allowed = bool(source.enabled and supported)
            local_state = {"serial": 0}
            for verify, label in ((False, "Aperçu local…"), (True, "Vérifier les fichiers…")):
                button = self.action_button(label, "document-properties-symbolic")
                button.set_sensitive(allowed)
                button.set_valign(Gtk.Align.CENTER)
                button.set_tooltip_text(
                    "Vérifier les empreintes SHA de ce compte, hors réseau et en lecture seule"
                    if verify else "Consulter les liens de ce compte sans lire les fichiers ni accéder au réseau"
                )
                def preview_numilog(
                    control, sid=source.source_id, output=local_row,
                    verify_files=verify, permitted=allowed, captured_scope=scope,
                    state=local_state,
                ):
                    if not permitted or not sync_action_scope_current(self, captured_scope):
                        return
                    state["serial"] += 1
                    serial = state["serial"]
                    output.set_subtitle("Inspection locale en cours (lecture seule)…")
                    def completed(result):
                        if state["serial"] == serial and sync_action_scope_current(self, captured_scope):
                            output.set_subtitle(sync_numilog_local_preview_presentation(result))
                    def failed(_error):
                        if state["serial"] == serial and sync_action_scope_current(self, captured_scope):
                            output.set_subtitle("Aperçu local indisponible. Aucun téléchargement autorisé.")
                    self.runner.submit(
                        "Vérification locale Numilog…" if verify_files else "Aperçu local Numilog…",
                        lambda: broker.preview_local_materialization(sid, verify_local_files=verify_files)
                        if sync_action_scope_current(self, captured_scope) else None,
                        completed, failed, control=control,
                        priority=TaskPriority.USER_ACTION, kind="sync-preflight",
                    )
                button.connect("clicked", preview_numilog)
                add_sync_action(local_row, button)
            add_sync_detail(group, local_row)

            # Numilog materialization is NOT inventory synchronization. Never
            # infer consent from source enabled, the Kobo switch, or preview.
            # An explicit SHA preview and a second user confirmation are both
            # mandatory before the real startup worker may acquire anything.
            consent_read = getattr(broker, "numilog_materialization_schedule", None)
            consent_prepare = getattr(broker, "prepare_numilog_materialization_consent", None)
            consent_confirm = getattr(broker, "confirm_numilog_materialization_consent", None)
            consent_revoke = getattr(broker, "revoke_numilog_materialization_consent", None)
            consent_supported = all(callable(method) for method in (
                consent_read, consent_prepare, consent_confirm, consent_revoke,
            ))
            consent_mode = "unavailable"
            if consent_supported:
                try:
                    value = consent_read(source.source_id)
                    if value in {"manual", "on_start"}:
                        consent_mode = value
                except Exception:
                    pass
            # Read only local scoped state; never fetch the provider,
            # hash files or resolve ADEPT merely to render a page.
            # This controller is also executed by the headless GTK harness;
            # absolute import avoids an unbound __package__ in that runner.
            from cifereca.gui.numilog_materialization_status import describe_run
            consent_history = ""
            consent_diagnostics = ""
            status_reader = getattr(broker, "numilog_materialization_run_status", None)
            if callable(status_reader):
                try:
                    summary = status_reader(source.source_id)
                    consent_history = describe_run(
                        consent_mode, summary, compact=True,
                    )
                    consent_diagnostics = describe_run(consent_mode, summary)
                except Exception:
                    consent_history = "Dernier bilan indisponible."
            consent_row = Adw.ActionRow()
            consent_row.set_title("Sauvegarde automatique des achats Numilog")
            consent_row.set_subtitle_lines(0)
            if consent_mode == "on_start":
                consent_subtitle = (
                    "Autorisé pour ce compte au prochain démarrage : jusqu’à "
                    "8 livres par ouverture, espacées d’au moins 60 minutes. "
                    "Les droits et fichiers sont revérifiés avant téléchargement."
                )
            elif consent_mode == "manual":
                consent_subtitle = (
                    "Mode manuel (par défaut). Aucun téléchargement automatique. "
                    "Consentement distinct de l’actualisation des notices."
                )
            else:
                consent_subtitle = (
                    "Sauvegarde planifiée indisponible. Aucun téléchargement automatique."
                )
            consent_row.set_subtitle(
                consent_subtitle + (" " + consent_history if consent_history else "")
            )
            consent_button = self.action_button(
                "Désactiver la sauvegarde" if consent_mode == "on_start"
                else "Activer au démarrage…",
                "document-save-symbolic",
            )
            consent_button.set_sensitive(
                consent_supported
                and consent_mode in {"manual", "on_start"}
                and (consent_mode == "on_start"
                     or (source.enabled and source.connected and source.sync_capable))
            )
            consent_button.set_valign(Gtk.Align.CENTER)
            consent_button.set_tooltip_text(
                "Autoriser ou révoquer la sauvegarde de livres pour ce seul compte. "
                "L’actualisation des notices reste une option indépendante."
            )
            add_sync_action(consent_row, consent_button)
            group.add(sync_action_row(consent_row))
            if consent_diagnostics and consent_diagnostics != consent_history:
                # The headline is concise; all observed counters, task refs,
                # item decisions and schedule remain inspectable in this
                # account's own initially-collapsed diagnostic disclosure.
                disclosure = Gtk.Expander(label="Détails et diagnostics · sauvegarde")
                disclosure.set_expanded(False)
                disclosure.set_margin_start(12)
                disclosure.set_margin_end(12)
                disclosure.set_margin_bottom(12)
                diagnostic_text = Gtk.Label(label=consent_diagnostics, xalign=0)
                diagnostic_text.set_wrap(True)
                diagnostic_text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                diagnostic_text.set_width_chars(1)
                diagnostic_text.set_hexpand(True)
                disclosure.set_child(diagnostic_text)
                group.add(disclosure)

            def change_numilog_consent(
                button, sid=source.source_id, expected_identity=source.identity_id,
                label=display_label, prior=consent_mode, output=consent_row,
                captured_scope=scope,
            ):
                def current():
                    return (
                        not getattr(self, "_closed", False)
                        and sync_action_scope_current(self, captured_scope)
                        and getattr(self.service.current_identity, "identity_id", None)
                            == expected_identity
                    )
                if not current():
                    return
                try:
                    if consent_read(sid) != prior:
                        self._refresh_sync_page()
                        return
                except Exception:
                    output.set_subtitle("Consentement indisponible. Aucun changement effectué.")
                    return

                def finish(result, expected):
                    if not current():
                        return
                    facts = getattr(result, "facts", None)
                    if (getattr(result, "ok", None) is True
                            and type(facts) is dict
                            and facts.get("status") == expected
                            and facts.get("acquisition_authorized") is False):
                        self._refresh_sync_page()
                    else:
                        output.set_subtitle("Consentement non enregistré. Aucun nouveau téléchargement autorisé.")

                if prior == "on_start":
                    dialog = Adw.MessageDialog.new(
                        self, "Désactiver la sauvegarde Numilog ?",
                        f"Compte : {label}.\n\nLa révocation empêche les nouveaux "
                        "téléchargements programmés pour ce compte. "
                        "Les fichiers déjà acquis sont conservés."
                    )
                    dialog.add_response("cancel", "Annuler")
                    dialog.add_response("revoke", "Désactiver")
                    dialog.set_response_appearance(
                        "revoke", Adw.ResponseAppearance.DESTRUCTIVE,
                    )
                    def revoke_response(_dialog, choice):
                        if choice != "revoke" or not current():
                            return
                        output.set_subtitle("Révocation de la sauvegarde en cours…")
                        def work():
                            return consent_revoke(sid) if current() else None
                        def done(result):
                            if current() and result == "manual":
                                self._refresh_sync_page()
                            elif current():
                                output.set_subtitle("Révocation indisponible. Vérifiez la politique.")
                        def failed(_error):
                            if current():
                                output.set_subtitle("Révocation indisponible. Vérifiez la politique.")
                        self.runner.submit(
                            "Désactivation sauvegarde Numilog…", work,
                            done, failed, control=button,
                            priority=TaskPriority.USER_ACTION, kind="source-consent",
                        )
                    dialog.connect("response", revoke_response)
                    dialog.present()
                    return
                if prior != "manual":
                    return
                output.set_subtitle("Vérification SHA locale en cours avant consentement…")

                def prepared(result):
                    if not current():
                        return
                    facts = getattr(result, "facts", None)
                    if (getattr(result, "ok", None) is not True
                            or type(facts) is not dict
                            or facts.get("status") != "confirmation_required"
                            or facts.get("acquisition_authorized") is not False
                            or facts.get("requires_explicit_confirmation") is not True
                            or facts.get("manifest_refreshed") is not False
                            or facts.get("files_verified") is not True
                            or facts.get("schedule_available") is not True):
                        output.set_subtitle("Préparation non attestée. Aucun consentement enregistré.")
                        return
                    token, counts = facts.get("confirmation_token"), facts.get("counts")
                    keys = (
                        "owned_purchases", "already_local_linked",
                        "already_local_verified", "needs_file_verification",
                        "potentially_missing", "candidate_unverified",
                        "needs_reconciliation", "attention_required",
                        "not_downloadable", "ignored_unattested_rights",
                    )
                    if (type(token) is not str or len(token) != 64
                            or any(ch not in "0123456789abcdef" for ch in token)
                            or type(counts) is not dict
                            or set(counts) != set(keys)
                            or any(type(counts[k]) is not int or not 0 <= counts[k] <= 100000
                                   for k in keys)):
                        output.set_subtitle("Preuve de consentement invalide. Aucun changement.")
                        return
                    summary = (
                        f"Compte : {label} · achats : {counts['owned_purchases']} · "
                        f"déjà liés : {counts['already_local_linked']} · "
                        f"manquants possibles : {counts['potentially_missing']} · "
                        f"à rapprocher : {counts['needs_reconciliation']}."
                    )
                    output.set_subtitle(summary + " Confirmation nécessaire.")
                    dialog = Adw.MessageDialog.new(
                        self, "Autoriser les sauvegardes Numilog au démarrage ?",
                        summary + "\n\nAu prochain démarrage, jusqu’à 8 éditions "
                        "achetées pourront être téléchargées automatiquement pour "
                        "ce compte, au maximum une tentative par heure. Les droits "
                        "seront relus dans la bibliothèque privée et les fichiers "
                        "existants ne seront pas remplacés. Des ACSM peuvent "
                        "nécessiter un profil ADEPT. Les notices restent séparées."
                    )
                    dialog.add_response("cancel", "Annuler")
                    dialog.add_response("confirm", "Autoriser pour ce compte")
                    dialog.set_response_appearance(
                        "confirm", Adw.ResponseAppearance.SUGGESTED,
                    )
                    def response(_dialog, choice):
                        if choice != "confirm" or not current():
                            return
                        output.set_subtitle("Enregistrement du consentement Numilog…")
                        def work():
                            return consent_confirm(sid, token, mode="on_start") if current() else None
                        def failed(_error):
                            if current():
                                output.set_subtitle("Consentement indisponible. Aucun nouveau téléchargement autorisé.")
                        self.runner.submit(
                            "Confirmation sauvegarde Numilog…", work,
                            lambda result: finish(result, "on_start"), failed,
                            control=button, priority=TaskPriority.USER_ACTION,
                            kind="source-consent",
                        )
                    dialog.connect("response", response)
                    dialog.present()

                def failed(_error):
                    if current():
                        output.set_subtitle("Préparation indisponible. Aucun nouveau téléchargement autorisé.")
                self.runner.submit(
                    "Préparation du consentement Numilog…",
                    lambda: consent_prepare(sid) if current() else None,
                    prepared, failed, control=button,
                    priority=TaskPriority.USER_ACTION, kind="sync-preflight",
                )
            consent_button.connect("clicked", change_numilog_consent)

        if source.provider_id in {"kobo", "numilog"}:
            # Inventory consent is separate from local materialization.
            def inventory_current(expected_identity=source.identity_id, captured_scope=scope):
                return (
                    sync_action_scope_current(self, captured_scope)
                    and getattr(self.service.current_identity, "identity_id", None)
                        == expected_identity
                )

            inventory_provider = "Numilog" if source.provider_id == "numilog" else "Kobo"
            # #1346/#1905: an independent private-store policy for refreshing remote
            # inventory and rights. Never re-use the local acquisition toggle:
            # reading this policy makes no network request or download.
            inventory_reader = getattr(broker, "inventory_refresh_schedule", None)
            inventory_writer = getattr(broker, "set_inventory_refresh_schedule", None)
            inventory_mode = "unavailable"
            inventory_minutes = 60
            inventory_supported = callable(inventory_reader) and callable(inventory_writer)
            if inventory_supported:
                try:
                    raw_policy = inventory_reader(source.source_id)
                    if (
                        type(raw_policy) is dict
                        and raw_policy.get("mode") in {"manual", "on_start", "periodic"}
                        and type(raw_policy.get("interval_minutes")) is int
                        and 15 <= raw_policy["interval_minutes"] <= 10080
                    ):
                        inventory_mode = raw_policy["mode"]
                        inventory_minutes = raw_policy["interval_minutes"]
                except Exception:
                    # Never reflect provider-owned paths, URLs or credentials.
                    pass

            inventory_row = Adw.ActionRow()
            inventory_row.set_title(f"Actualisation automatique des notices {inventory_provider}")
            inventory_row.set_subtitle_lines(0)
            if inventory_mode == "manual":
                inventory_row.set_subtitle(
                    "Mode manuel (par défaut) : notices et droits à la demande. "
                    "Aucun téléchargement de fichier autorisé."
                )
            elif inventory_mode == "on_start":
                inventory_row.set_subtitle(
                    f"Actualisation des notices au démarrage autorisée "
                    f"(intervalle minimal : {inventory_minutes} min). "
                    "N'autorise aucun téléchargement."
                )
            elif inventory_mode == "periodic":
                inventory_row.set_subtitle(
                    f"Actualisation des notices périodique autorisée "
                    f"(toutes les {inventory_minutes} min, si ordonnanceur actif). "
                    "N'autorise aucun téléchargement."
                )
            else:
                inventory_row.set_subtitle(
                    "Planification des notices indisponible. "
                    "Aucune synchronisation automatique autorisée ici."
                )
            inventory_button = self.action_button(
                "Désactiver les actualisations" if inventory_mode in {"on_start", "periodic"}
                else "Planifier les notices…",
                "preferences-system-symbolic",
            )
            inventory_button.set_sensitive(bool(
                inventory_supported and (
                    inventory_mode in {"on_start", "periodic"}
                    or (inventory_mode == "manual" and source.enabled
                        and source.connected and source.sync_capable
                        and "remote_inventory" in source.sync_capabilities)
                )
            ))
            inventory_button.set_tooltip_text(
                "Modifie seulement la politique de notices/droits de ce compte. "
                "Aucune acquisition et aucun téléchargement."
            )
            inventory_button.set_valign(Gtk.Align.CENTER)
            add_sync_action(inventory_row, inventory_button)
            add_sync_detail(group, inventory_row)

            def change_inventory_schedule(
                button, sid=source.source_id, account_label=display_label,
                prior_mode=inventory_mode, prior_minutes=inventory_minutes,
                current=inventory_current, provider_label=inventory_provider,
            ):
                if not current() or prior_mode not in {"manual", "on_start", "periodic"}:
                    return

                def apply_mode(target):
                    if not current():
                        return
                    try:
                        fresh = inventory_reader(sid)
                        if (
                            type(fresh) is not dict
                            or fresh.get("mode") != prior_mode
                            or fresh.get("interval_minutes") != prior_minutes
                        ):
                            if current():
                                self._refresh_sync_page()
                            return
                        saved = inventory_writer(
                            sid, target, interval_minutes=prior_minutes,
                        )
                        if (
                            type(saved) is not dict
                            or saved.get("mode") != target
                            or saved.get("interval_minutes") != prior_minutes
                        ):
                            raise ValueError("invalid inventory schedule acknowledgement")
                    except Exception:
                        if current():
                            self.show_error(
                                f"Planification des notices {provider_label} indisponible.",
                                "Synchronisation",
                            )
                        return
                    if current():
                        self._refresh_sync_page()

                if prior_mode != "manual":
                    # Revocation is possible even after disconnect or rebinding
                    # and must not require a confirmation dialog.
                    apply_mode("manual")
                    return
                dialog = Adw.MessageDialog.new(
                    self, f"Planifier les notices {provider_label} ?",
                    f"Compte : {account_label}.\n\n"
                    "Ce réglage autorise uniquement la mise à jour des "
                    "notices des livres possédés et des droits du compte : "
                    "sans téléchargement EPUB/ACSM, sans ADEPT ni "
                    "acquisition. Le catalogue commercial public est une recherche "
                    "distincte. La cadence est limitée par le "
                    f"dernier essai et un intervalle de {prior_minutes} min. "
                    "L’actualisation périodique nécessite un ordonnanceur actif. "
                    "Vous pourrez revenir au mode manuel à tout moment.",
                )
                dialog.add_response("cancel", "Annuler")
                dialog.add_response("on_start", "Au démarrage")
                dialog.add_response("periodic", "Périodique")
                dialog.set_response_appearance(
                    "on_start", Adw.ResponseAppearance.SUGGESTED,
                )

                def on_response(_dialog, choice):
                    if choice in {"on_start", "periodic"} and current():
                        apply_mode(choice)

                dialog.connect("response", on_response)
                dialog.present()

            inventory_button.connect("clicked", change_inventory_schedule)

    # Personal inventories come first. Public and authenticated catalogue
    # searches remain an optional, collapsed second section.
    if sync_action_scope_current(self, scope):
        append_catalogue_searches()


def run_opening_sync(self, control: Gtk.Widget | None = None, *, deep: bool = False) -> None:
    # Connection completion schedules this via GLib.idle_add. The window
    # might have closed after scheduling but before this callback runs.
    if getattr(self, "_closed", False):
        return
    # Only startup_coordinator's scoped initial callback can set this flag.
    # The worker's future completion captures it, independent of a later
    # identity/profile switch or an unrelated manual opening command.
    first_opening = (
        getattr(self, "_source_inventory_startup_pending", False) is True
        and control is None and deep is False
    )

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
        def guard_count(key: str) -> int:
            values = [row.get(key) for row in summary.get("identities", [])]
            return sum(value for value in values
                       if type(value) is int and 0 <= value <= 1_000_000)

        borrower = guard_count("skipped_borrower_binding")
        cross_identity = guard_count("skipped_cross_identity")
        if acquisitions or attention or errors or borrower or cross_identity:
            message = f"Synchronisation : {acquisitions} acquisition(s), {attention} action(s), {errors} erreur(s)"
            reasons = []
            if borrower:
                reasons.append(f"{borrower} association(s) emprunteur à configurer")
            if cross_identity:
                reasons.append(f"{cross_identity} conflit(s) entre identités")
            if reasons:
                message += " · " + ", ".join(reasons)
            needs_attention = bool(attention or errors or borrower or cross_identity)
            self._activity_result(
                message, success=not needs_attention, persistent=needs_attention,
            )
        # The global opening worker has finished and released its process
        # acquisition lock. Only now may opted-in Kobo inventory sources
        # request a *separate* metadata/rights refresh. No opt-in => no task.
        if first_opening:
            from .source_inventory_startup import dispatch_after_opening
            dispatch_after_opening(self)
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
    """Retire the legacy GUI timer without launching a second Kobo batch.

    Opening synchronization has its own Core coordination and acquisition lock.
    A GUI idle callback must never start another global backup, including
    after a settings save or a delayed startup event. The interval is retained
    only for backwards-compatible configuration reads (#1346).
    """
    if getattr(self, "_closed", False):
        return
    if self._kobo_backup_timer_id is not None:
        try:
            GLib.source_remove(self._kobo_backup_timer_id)
        except Exception:
            pass
        self._kobo_backup_timer_id = None


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
                f"{summary.get('skipped', 0)} ignoré(s), "
                f"{summary.get('skipped_archived', 0)} archivé(s) ignoré(s), "
                f"{attention} action(s), {errors} erreur(s)"
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
