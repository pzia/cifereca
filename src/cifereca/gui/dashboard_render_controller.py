"""P0 #1051: render current identity overview without retaining GTK ownership.

The host controls lifetime and service snapshot; Gtk and CSS styling are
explicitly injected, and plugin overview notifications are not duplicated.
No activation, subscriptions, provider traffic or implicit rescan is started.
"""
from __future__ import annotations

from datetime import datetime

def render_dashboard(window, *, Gtk, add_css) -> None:
    if getattr(window, '_closed', False):
        return
    if hasattr(window,'_refresh_statistics'):window._refresh_statistics()
    for callback in list(getattr(window, "_overview_listeners", [])):
        try: callback()
        except Exception as exc: window.diagnostics.add("UI", "identity overview callback", "error", detail={"error": type(exc).__name__})
    if not hasattr(window, "dashboard_cards"):
        return
    child = window.dashboard_cards.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling(); window.dashboard_cards.remove(child); child = nxt
    for card in window.service.dashboard_state():
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(14); box.set_margin_end(14)
        add_css(box, "cover-frame")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Button(label=str(card.get("label") or "Utilisateur")); name.set_hexpand(True)
        name.get_child().set_wrap(True)
        name.set_tooltip_text("Sélectionner cette identité et ouvrir sa bibliothèque")
        name.connect("clicked", lambda _b, iid=card["identity_id"]: window.open_identity_library(iid))
        add_css(name, "flat", "book-title"); head.append(name)
        sync = card.get("sync") or {}
        badge = Gtk.Label(label=window._sync_status_label(sync.get("status"))); add_css(badge, "badge"); head.append(badge)
        box.append(head)
        stats=card.get('statistics') or {}
        if stats:
            loans=stats.get('known_loans')
            count=Gtk.Label(label=f"{stats.get('local_books',0)} livres locaux · {loans if loans is not None else '—'} emprunts connus",xalign=0)
            count.set_wrap(True);box.append(count)
            actions=stats.get('actions') or {}
            if actions:
                ok=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_succeeded','imports_succeeded','synchronizations_succeeded','transformations_succeeded','transfers_succeeded'))
                failed=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_failed','imports_failed','synchronizations_failed','transformations_failed','transfers_failed'))
                action_label=Gtk.Label(label=f"Actions (30 j) : {ok} réussies · {failed} échouées",xalign=0)
                action_label.set_wrap(True); add_css(action_label, 'caption', 'dim-label'); box.append(action_label)
        providers = ", ".join(source["label"] + ("" if source["connected"] else " (à connecter)") for source in card.get("sources", [])) or "Aucune source connectée"
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
                issues.connect("clicked", lambda _b, c=card: window._show_sync_issues(c)); box.append(issues)
        box.set_size_request(300, -1)
        for capability in card.get("capabilities", []):
            label = Gtk.Label(label="✓ " + capability["label"], xalign=0); add_css(label, "caption")
            box.append(label)
        window.dashboard_cards.insert(box, -1)


