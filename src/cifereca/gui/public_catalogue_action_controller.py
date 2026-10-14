"""Native action for a public notice or generic catalogue (#1129).

The Core service remains the only authority for public URL classification.
The MainWindow facade owns UI actions and Gtk; Gio is injected. No plugin
code, network lookup or identity-mutating behaviour is introduced.
"""
from __future__ import annotations


def add_catalogue_action(window, detail, item, *, Gio):
    if getattr(window, "_closed", False):
        return
    target = window.service.public_catalogue_target(item)
    if not target:
        return
    kind, url = target
    # If the document link is already rendered, do not duplicate it as an
    # action in the same details panel.
    if url in getattr(detail, "_rendered_document_urls", set()):
        return
    is_notice = kind == "notice"
    button = window.action_button(
        "Ouvrir la notice" if is_notice else "Ouvrir le catalogue",
        "web-browser-symbolic",
    )
    button._cifereca_action_id = "open-notice" if is_notice else "open-catalogue"
    button.set_tooltip_text(
        "Ouvrir la page publique de ce livre"
        if is_notice else "Ouvrir le catalogue général pour y rechercher ce livre"
    )
    generation = getattr(window, "_identity_context_generation", None)

    def open_catalogue(_button):
        if getattr(window, "_closed", False):
            return
        if getattr(window, "_identity_context_generation", None) != generation:
            return
        try:
            # Re-evaluate the Core public target at click time; never allow a
            # rendered notice action to silently become a generic homepage.
            current = window.service.public_catalogue_target(item)
            if current and current[0] == kind:
                Gio.AppInfo.launch_default_for_uri(current[1], None)
            elif current:
                window.toast("La destination du lien a changé ; actualisez la fiche.")
        except Exception as exc:
            if not getattr(window, "_closed", False):
                window.show_error(
                    str(exc), "Ouverture de la page publique impossible"
                )

    button.connect("clicked", open_catalogue)
    detail.add_actions([button])
