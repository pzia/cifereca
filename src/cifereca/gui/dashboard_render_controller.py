"""P0 #1051: render current identity overview without retaining GTK ownership.

The host controls lifetime and service snapshot; Gtk and CSS styling are
explicitly injected, and plugin overview notifications are not duplicated.
No activation, subscriptions, provider traffic or implicit rescan is started.
"""
from __future__ import annotations

from datetime import datetime

def _dashboard_count(stats: dict, key: str) -> str:
    """In a dashboard snapshot, unknown is not a real count of zero."""
    value = stats.get(key)
    return str(value) if type(value) is int and value >= 0 else "—"


# The dashboard has the same source applicability rule as the public
# Accueil overview (#1867), but must not import a plugin/private Core service.
# Unknown sources fail closed to existing 'prêts non actualisés' semantics.
_NON_LENDING_SOURCES = frozenset({
    "kobo", "numilog", "numilog-catalog", "gallica", "gutenberg",
    "bnf-catalogue", "baen", "ebooksgratuits", "local",
})


def _dashboard_has_no_loan_source(card: object, stats: object) -> bool:
    if type(card) is not dict or type(stats) is not dict:
        return False
    providers = card.get("providers")
    if (type(providers) not in (list, tuple, set, frozenset)
            or not providers or len(providers) > 128
            or any(type(pid) is not str or pid not in _NON_LENDING_SOURCES
                   for pid in providers)):
        return False
    caps = card.get("capabilities")
    if isinstance(caps, (list, tuple)) and any(
        type(cap) is dict and cap.get("id") == "loans" for cap in caps
    ):
        return False
    observed = stats.get("loan_sources_observed")
    known = stats.get("known_loans")
    # Historic loan evidence survives a change in configured providers.
    return not ((type(observed) is int and observed > 0)
                or (type(known) is int and known > 0))


def _dashboard_source_label(source: object) -> str:
    # Presentation only. A store account does not attest a downloaded file,
    # and the Kobo catalogue is not a second configured account.
    if type(source) is not dict:
        return "Source inconnue"
    name = str(source.get("label") or "Source inconnue")
    pid = source.get("provider_id")
    if pid == "kobo":
        return name + " (Mes achats / Catalogue)"
    if pid == "numilog":
        return name + " (Mes achats)"
    if pid == "numilog-catalog":
        return name if "catalogue" in name.casefold() else name + " (Catalogue public)"
    return name


def render_dashboard(window, *, Gtk, add_css) -> None:
    if getattr(window, '_closed', False):
        return
    token = object()
    window._dashboard_render_token = token

    def is_current():
        return (
            not getattr(window, "_closed", False)
            and getattr(window, "_dashboard_render_token", None) is token
        )

    if hasattr(window, '_refresh_statistics'):
        window._refresh_statistics()
    if not is_current():
        return

    # A plugin may unsubscribe, replace the entire listener list or dispose
    # MainWindow while handling the first notification. Snapshot iteration is
    # safe only while the original callback is still actively registered.
    for callback in list(getattr(window, "_overview_listeners", [])):
        if not is_current():
            return
        if not any(listener is callback for listener in getattr(window, "_overview_listeners", ())):
            continue
        try:
            callback()
        except Exception as exc:
            if is_current():
                window.diagnostics.add(
                    "UI", "identity overview callback", "error",
                    detail={"error": type(exc).__name__},
                )
    if not is_current() or not hasattr(window, "dashboard_cards"):
        return
    child = window.dashboard_cards.get_first_child()
    while child is not None:
        if not is_current():
            return
        nxt = child.get_next_sibling()
        window.dashboard_cards.remove(child)
        child = nxt
    if not is_current():
        return
    cards = window.service.dashboard_state()
    if not is_current():
        return
    for card in cards:
        if not is_current():
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(14); box.set_margin_end(14)
        add_css(box, "cover-frame")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Button(label=str(card.get("label") or "Utilisateur")); name.set_hexpand(True)
        name.get_child().set_wrap(True)
        name.set_tooltip_text("Sélectionner cette identité et ouvrir sa bibliothèque")
        name.connect("clicked", lambda _b, iid=card["identity_id"]: (
            window.open_identity_library(iid) if is_current() else None
        ))
        add_css(name, "flat", "book-title"); head.append(name)
        sync = card.get("sync") or {}
        badge = Gtk.Label(label=window._sync_status_label(sync.get("status"))); add_css(badge, "badge"); head.append(badge)
        box.append(head)
        stats=card.get('statistics') or {}
        if stats:
            local = _dashboard_count(stats, "local_books")
            loans = _dashboard_count(stats, "known_loans")
            owned = _dashboard_count(stats, "remote_owned_rights")
            loan_label = (
                "aucune source de prêt configurée"
                if _dashboard_has_no_loan_source(card, stats)
                else f"{loans} prêts de bibliothèque connus"
            )
            count=Gtk.Label(label=f"{local} livres locaux · {loan_label}",xalign=0)
            count.set_wrap(True);box.append(count)
            # A distant store entitlement is neither a library loan nor
            # evidence of a downloaded EPUB. Never add these three counts.
            rights=Gtk.Label(
                label=f"{owned} droits d’achat distants · fichier local non présumé",
                xalign=0,
            )
            rights.set_wrap(True);box.append(rights)
            actions=stats.get('actions') or {}
            if actions:
                ok=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_succeeded','imports_succeeded','synchronizations_succeeded','transformations_succeeded','transfers_succeeded'))
                failed=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_failed','imports_failed','synchronizations_failed','transformations_failed','transfers_failed'))
                action_label=Gtk.Label(label=f"Actions (30 j) : {ok} réussies · {failed} échouées",xalign=0)
                action_label.set_wrap(True); add_css(action_label, 'caption', 'dim-label'); box.append(action_label)
        providers = ", ".join(
            _dashboard_source_label(source) + (
                "" if source.get("connected") is True
                else " (recherche indisponible)"
                if source.get("provider_id") == "numilog-catalog"
                else " (à connecter)"
            )
            for source in card.get("sources", [])
            if type(source) is dict
        ) or "Aucune source connectée"
        source_label = Gtk.Label(label=f"Sources : {providers}", xalign=0); source_label.set_wrap(True); box.append(source_label)
        profile = "Lié" if card.get("adept_profile_id") else "À configurer"
        box.append(Gtk.Label(label=f"Profil Adobe : {profile}", xalign=0))
        deep_last_at = card.get("deep_last_at")
        if deep_last_at:
            try:
                deep_label = datetime.fromtimestamp(int(deep_last_at)).strftime("%d/%m/%Y %H:%M")
            except Exception:
                deep_label = str(deep_last_at)
            lab = Gtk.Label(label=f"Dernière vérification complète : {deep_label}", xalign=0); add_css(lab, "caption", "dim-label"); box.append(lab)
        if sync:
            sync_detail = sync.get("detail") or {}
            provisioning = sync_detail.get("adept_provisioning") or {}
            if provisioning.get("state"):
                box.append(Gtk.Label(label=f"Provisioning : {provisioning.get('state')}", xalign=0))
            details = f"Nouvelles acquisitions : {sync.get('new_acquisitions', 0)} · Actions : {sync.get('attention_required', 0)} · Erreurs : {sync.get('errors', 0)}"
            lab = Gtk.Label(label=details, xalign=0); lab.set_wrap(True); add_css(lab, "caption", "dim-label"); box.append(lab)
            if sync.get("errors") or sync.get("attention_required"):
                issues = Gtk.Button(label="Voir les erreurs et actions")
                issues.connect("clicked", lambda _b, c=card: (
                    window._show_sync_issues(c) if is_current() else None
                ))
                box.append(issues)
        box.set_size_request(300, -1)
        for capability in card.get("capabilities", []):
            label = Gtk.Label(label="✓ " + capability["label"], xalign=0); add_css(label, "caption")
            box.append(label)
        window.dashboard_cards.insert(box, -1)


