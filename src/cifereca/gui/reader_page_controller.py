"""P1 #2854: Liseuses page using the existing GTK MasterDetailPage.

Only previously loaded Library entries plus identity-scoped SQLite snapshots
are read. No USB scan, device mount, provider request, artifact copy/delete,
automatic library refresh or new entitlement is started on navigation.
"""
from __future__ import annotations

import time

from .detail_model import book_detail_presentation
from .reader_library_projection import project_reader_library


FILTERS = ("all", "to_send", "to_remove", "without_intent")
FILTER_LABELS = ("Tous", "À mettre", "À retirer", "Sans intention")


def build_readers_page(window, *, Gtk, MasterDetailPage):
    page = MasterDetailPage(
        window, "Liseuses",
        "Souhaits par appareil · aucun transfert automatique",
        "Aucun livre local disponible",
        "Les livres déjà chargés dans Bibliothèque apparaissent ici. "
        "Aucune présence USB n'est présumée.",
    )
    chooser_label = Gtk.Label(label="Appareil", xalign=0)
    page.toolbar.append(chooser_label)
    chooser = Gtk.DropDown.new_from_strings(["Aucune liseuse configurée"])
    chooser.set_hexpand(True)
    chooser.set_tooltip_text(
        "Liseuses USB configurées pour le seul profil actuellement actif."
    )
    page.toolbar.append(chooser)
    filter_label = Gtk.Label(label="État souhaité", xalign=0)
    page.toolbar.append(filter_label)
    filter_chooser = Gtk.DropDown.new_from_strings(list(FILTER_LABELS))
    filter_chooser.set_tooltip_text(
        "Filtrer les souhaits enregistrés, jamais la présence réelle des fichiers."
    )
    page.toolbar.append(filter_chooser)

    notice = Gtk.Label(label="", xalign=0)
    notice.set_wrap(True)
    notice.set_margin_start(18)
    notice.set_margin_end(18)
    notice.set_margin_bottom(8)
    # Place status between toolbar and pane, visible on narrow widths.
    page.insert_child_after(notice, page.toolbar)
    window._reader_status_label = notice
    window._reader_device_dropdown = chooser
    window._reader_filter_dropdown = filter_chooser
    window._reader_device_ids = ()
    window._reader_items = {}
    window._reader_source_items = {}
    window._reader_scope_identity = None
    window._reader_filter = "all"
    window._reader_selection_generation = 0
    window._reader_library_snapshot = None
    window._reader_usb_decision_details = None
    window._reader_usb_detail_source_snapshot = None

    def select_reader(_dropdown, _pspec):
        if (getattr(window, "_closed", False)
                or getattr(window, "_readers_rendering", False)):
            return
        index = chooser.get_selected()
        ids = getattr(window, "_reader_device_ids", ())
        if not type(index) is int or index >= len(ids):
            return
        if getattr(window, "_reader_selected_device_id", None) != ids[index]:
            # A→B→A in one route must revoke an earlier in-flight dry-run.
            window._reader_selection_generation += 1
        window._reader_selected_device_id = ids[index]
        window._render_readers()

    def select_filter(_dropdown, _pspec):
        if (getattr(window, "_closed", False)
                or getattr(window, "_readers_rendering", False)):
            return
        index = filter_chooser.get_selected()
        if type(index) is not int or not 0 <= index < len(FILTERS):
            return
        window._reader_filter = FILTERS[index]
        window._render_readers()

    chooser.connect("notify::selected", select_reader)
    filter_chooser.connect("notify::selected", select_filter)
    page.connect_selected(lambda row: reader_selected(window, row))
    refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
    refresh.set_tooltip_text(
        "Actualiser uniquement les souhaits locaux enregistrés. "
        "Ne scanne pas la liseuse ni les fournisseurs."
    )
    refresh.connect("clicked", lambda _button: window._render_readers())
    page.header_actions.append(refresh)
    # Voluntary action only: never scan during page construction or routing.
    inspect = Gtk.Button.new_from_icon_name("document-properties-symbolic")
    inspect._cifereca_action_id = "reader-usb-dry-run"
    inspect.set_tooltip_text(
        "Examiner les changements : choisir le volume USB monté. "
        "Analyse en arrière-plan, sans transfert ni suppression."
    )
    def examine_clicked(button):
        from .reader_usb_preview_actions import request_reader_usb_preview
        request_reader_usb_preview(window, button, Gtk=Gtk)
    inspect.connect("clicked", examine_clicked)
    page.header_actions.append(inspect)
    return page


def render_readers(window, *, Gtk, BookListItem) -> None:
    """Render from cached Library and SQLite, without per-row I/O.

    This function may be called by navigation, filter change or an explicit
    local refresh. Never turn a missing reader or old observation into a
    confirmed book on the device.
    """
    if getattr(window, "_closed", False) or getattr(window, "_readers_rendering", False):
        return
    page = getattr(window, "readers_page", None)
    if page is None:
        return
    window._readers_rendering = True
    try:
        window._reader_items = {}
        window._reader_source_items = {}
        window._reader_library_snapshot = None
        window._reader_usb_decision_details = None
        window._reader_usb_detail_source_snapshot = None
        current = getattr(getattr(window, "service", None), "current_identity", None)
        identity_id = getattr(current, "identity_id", None)
        storage = getattr(getattr(window, "service", None), "storage", None)
        if type(identity_id) is not str or not identity_id or storage is None:
            page.show_rows([])
            window._reader_status_label.set_label(
                "Choisissez un profil pour consulter ses liseuses."
            )
            window._reader_scope_identity = None
            return
        try:
            configured = storage.device_bindings(identity_id=identity_id)
            devices = [
                row for row in configured
                if type(row) is dict and row.get("identity_id") == identity_id
                and type(row.get("device_id")) is str and row["device_id"]
                and type(row.get("metadata")) is dict
                and type(row["metadata"].get("usb_fingerprint")) is str
                and 8 <= len(row["metadata"]["usb_fingerprint"]) <= 128
            ]
            if len(devices) > 16:
                raise ValueError("too many configured readers")
            ids = tuple(row["device_id"] for row in devices)
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate reader binding")
        except Exception:
            page.show_rows([])
            window._reader_status_label.set_label(
                "Liseuses indisponibles : vérifiez la configuration du profil."
            )
            return

        # Never retain a selection from Alice when the active identity is Bob,
        # even if both identities happen to have reader-0 or the same SHA.
        if getattr(window, "_reader_scope_identity", None) != identity_id:
            window._reader_selected_device_id = None
        window._reader_scope_identity = identity_id
        selected = getattr(window, "_reader_selected_device_id", None)
        if selected not in ids:
            selected = ids[0] if ids else None
        if getattr(window, "_reader_selected_device_id", None) != selected:
            window._reader_selection_generation += 1
        window._reader_selected_device_id = selected

        if ids != getattr(window, "_reader_device_ids", ()):
            labels = [
                (row.get("label") if type(row.get("label")) is str
                 and row["label"].isprintable() and row["label"]
                 else f"Liseuse {index}")
                for index, row in enumerate(devices, start=1)
            ] or ["Aucune liseuse configurée"]
            window._reader_device_dropdown.set_model(Gtk.StringList.new(labels))
            window._reader_device_ids = ids
        if selected is not None:
            window._reader_device_dropdown.set_selected(ids.index(selected))
        if not devices:
            page.show_rows([])
            window._reader_status_label.set_label(
                "Aucune liseuse USB configurée pour ce profil. "
                "Les préférences et les intentions sont séparées des transferts."
            )
            return

        view_filter = getattr(window, "_reader_filter", "all")
        if view_filter not in FILTERS:
            view_filter = "all"
        window._reader_filter = view_filter
        window._reader_filter_dropdown.set_selected(FILTERS.index(view_filter))
        binding = devices[ids.index(selected)]
        try:
            local_rows = storage.placements(state="present")
            intentions = storage.device_placements(device_id=selected)
            cached = getattr(window, "_library_entries_cache", ())
            # No auto-read of remote catalogues or file hashes on each
            # navigation. Library owns the authoritative cached book list.
            if type(cached) not in (list, tuple):
                cached = ()
            snapshot = project_reader_library(
                identity_id=identity_id, binding=binding,
                local_placements=local_rows, device_placements=intentions,
                library_entries=cached, view_filter=view_filter,
            )
        except Exception:
            page.show_rows([])
            window._reader_status_label.set_label(
                "État des liseuses à vérifier : aucune présence USB confirmée."
            )
            return
        entries = {
            item.get("key"): item
            for item in cached
            if type(item) is dict and item.get("identity_id") == identity_id
            and type(item.get("key")) is str
        }
        rows = []
        for item in snapshot.books:
            rows.append(BookListItem(
                item.key, item.title, item.author, item.label,
                "Bibliothèque locale", item.thumbnail_url,
            ))
            window._reader_items[item.key] = item
            source = entries.get(item.key)
            if source is not None:
                window._reader_source_items[item.key] = source
        window._reader_library_snapshot = snapshot
        page.show_rows(rows, total_count=len(rows))
        suffix = (
            " · Appareil désactivé"
            if not snapshot.device_enabled else ""
        )
        window._reader_status_label.set_label(
            f"{snapshot.total_local} livre(s) visibles · "
            f"{snapshot.wanted_keep} à mettre · "
            f"{snapshot.wanted_remove} à retirer · "
            f"{snapshot.no_intention} sans intention · "
            "Présence USB actuelle non confirmée" + suffix
        )
    finally:
        window._readers_rendering = False


def reader_selected(window, row) -> None:
    if getattr(window, "_closed", False):
        return
    page = getattr(window, "readers_page", None)
    if page is None:
        return
    if row is None:
        page.detail.empty(
            "Sélectionnez un livre",
            "Choisissez un livre local pour consulter son souhait sur cette liseuse.",
        )
        return
    current = getattr(getattr(window, "service", None), "current_identity", None)
    identity_id = getattr(current, "identity_id", None)
    if not identity_id or identity_id != getattr(window, "_reader_scope_identity", None):
        page.detail.empty("Profil modifié", "Rouvrez la page pour vérifier l'identité.")
        return
    item = getattr(window, "_reader_items", {}).get(row.key)
    if item is None:
        return
    status = (
        "Jamais confirmé" if item.last_known == "unknown"
        else f"{'Présent' if item.last_known == 'present' else 'Absent'} "
             "(ancienne observation, à revérifier)"
    )
    page.detail.show_presentation(book_detail_presentation(
        title=item.title,
        subtitle=item.author,
        thumbnail=item.thumbnail_url,
        badges=(),
        section_rows=[
            ("Liseuse", [
                ("Souhait", {
                    "none": "Aucune intention",
                    "keep": "Mettre sur la liseuse",
                    "remove": "Retirer de la liseuse",
                }[item.desired_state]),
                ("Présence actuelle", "Non confirmée"),
                ("Dernier état enregistré", status),
                ("Dernière observation (timestamp)",
                 str(item.last_observed_at) if item.last_observed_at is not None
                 else "Aucune"),
            ]),
        ],
    ))
    # A dry-run is a temporary observation of an exact list snapshot. It is
    # neither an instruction to deliver/remove nor a persistent presence
    # indicator. Filter/profile/device refresh clears it atomically.
    from .reader_usb_decisions import ReaderUsbDecisionDetails
    details = getattr(window, "_reader_usb_decision_details", None)
    snapshot = getattr(window, "_reader_library_snapshot", None)
    if (type(details) is ReaderUsbDecisionDetails
            and details.status == "scanned"
            and details.executable is False
            and snapshot is getattr(window, "_reader_usb_detail_source_snapshot", None)
            and details.identity_id == identity_id
            and details.device_id == getattr(window, "_reader_selected_device_id", None)
            and type(details.observed_at) is int
            and 0 <= int(time.time()) - details.observed_at <= 300):
        decision = next(
            (candidate for candidate in details.books
             if candidate.book_key == item.key
             and candidate.executable is False),
            None,
        )
        if decision is not None:
            page.detail.add_group("Dernier examen USB · lecture seule", [
                ("Décision", decision.state_label),
                ("Motif", decision.reason_label),
                ("Observation ponctuelle", decision.current_label),
                ("Validité", "Aperçu temporaire · aucun transfert ou retrait"),
            ])
    original = getattr(window, "_reader_source_items", {}).get(row.key)
    if (type(original) is dict
            and original.get("identity_id") == identity_id):
        # The existing Library detail owns the same Open/Mettre/Retirer
        # actions, preserving Core revalidation and no automatic transfer.
        # Prefer the actual local placement proven by the snapshot over a
        # catalogue card's optional/stale placement identifier.
        scoped = dict(original)
        scoped.update({
            "placement_id": item.placement_id,
            "artifact_id": item.artifact_id,
            "identity_id": identity_id,
            "local": True,
        })
        window._add_local_book_actions(page.detail, scoped)


__all__ = ["build_readers_page", "render_readers", "reader_selected"]
