"""Explicit GTK-bound detail facet adapter (#1129).

MainWindow owns the widgets, filter callbacks and ADEPT capability. The
facade injects GTK/Adwaita and task priority; imports remain Core/headless.
No new facet extraction/lookup engine is introduced.
"""
from __future__ import annotations


def add_facets(window, detail, item, controls, *, Adw, Gtk, TaskPriority):
    values = window._facet_values(item)
    value_map = {}
    for field, label, value in values:
        value_map.setdefault(field, []).append((label, value))

    row_fields = {
        "Source": "source",
        "Provenance": "source",
        "Fournisseur": "source",
        "Format": "format",
        "Support": "support",
        "Auteur": "author",
        "Éditeur": "publisher",
        "Sujets": "subject",
        "Sujet": "subject",
        "Collection / série": "collection",
        "Collection": "collection",
        "Série": "series",
        "ISBN": "isbn",
        "Identifiants": "isbn",
        "Langue": "language",
        "Langues": "language",
    }
    subtitle_fallback_fields = {
        "author", "publisher", "subject", "series", "collection", "language",
    }

    def bind_rows(widget):
        # A native GTK type/title guard survives GObject wrapper recreation:
        # each Détail row retains its own subtitle and Copier/Rechercher.
        if isinstance(widget, getattr(Adw, "ExpanderRow", ())) and widget.get_title() == "Détail":
            return
        if isinstance(widget, Adw.ActionRow):
            field = row_fields.get(widget.get_title())
            original_subtitle = getattr(
                widget,
                "_cifereca_facet_original_subtitle",
                widget.get_subtitle(),
            )
            widget._cifereca_facet_original_subtitle = original_subtitle

            previous = getattr(widget, "_cifereca_facet_values", None)
            if previous is not None:
                try:
                    widget.remove(previous)
                except (AttributeError, TypeError):
                    previous.set_visible(False)
                widget._cifereca_facet_values = None

            choices = list(value_map.get(field, ())) if field else []
            if not choices and field in subtitle_fallback_fields:
                displayed = str(original_subtitle or "").strip()
                if displayed and displayed != "—":
                    choices = [(displayed, displayed)]

            widget.set_activatable(False)
            if choices:
                widget.set_subtitle("")
                facet_values = window._facet_value_group(choices, field, controls)
                facet_values.set_tooltip_text(
                    "Valeurs filtrables : utilisez Tab puis Entrée ou Espace"
                )
                widget.add_suffix(facet_values)
                widget._cifereca_facet_values = facet_values
            else:
                widget.set_subtitle(original_subtitle)

        child = widget.get_first_child()
        while child:
            bind_rows(child)
            child = child.get_next_sibling()

    bind_rows(detail.content)

    subtitle = getattr(detail, "_subtitle_label", None)
    authors = list(value_map.get("author", ()))
    if subtitle is not None:
        author_box = getattr(subtitle, "_cifereca_author_facets", None)
        if author_box is not None:
            parent = author_box.get_parent()
            if parent is not None:
                parent.remove(author_box)
            subtitle._cifereca_author_facets = None
        subtitle.set_visible(True)
        if authors:
            parent = subtitle.get_parent()
            if parent is not None:
                subtitle.set_visible(False)
                author_box = window._facet_value_group(authors, "author", controls)
                author_box.add_css_class("detail-subtitle")
                parent.append(author_box)
                subtitle._cifereca_author_facets = author_box

    identity = window.service.current_identity
    binding = window.service.adept_return_for_artifact(
        str(item.get("artifact_id") or ""),
        identity.identity_id if identity else "",
    )
    if binding:
        button = Gtk.Button(label="Restituer le prêt ADEPT")
        button.set_tooltip_text(
            "Restituer avec le profil d’origine ; conserver le fichier local. "
            "Le retour fournisseur reste une action distincte."
        )
        def return_loan(_button):
            window.runner.submit(
                "Restitution du prêt ADEPT…",
                lambda: window.service.return_adept_acquisition(binding["acquisition_id"]),
                lambda _result: (
                    button.set_sensitive(False),
                    window._activity_result(
                        "Retour enregistré, jeton absent · fichier conservé"
                        + (
                            " · état local vérifié"
                            if _result.get("verification") == "confirmed"
                            else " · vérification locale non confirmée"
                        )
                    ),
                ),
                lambda exc: window.show_error(str(exc), "Restitution ADEPT impossible"),
                control=button,
                priority=TaskPriority.USER_ACTION,
            )
        button.connect("clicked", return_loan)
        detail.add_actions([button])
