"""#2385: GTK presentation of the authoritative six-mode public C read model.

No catalogue URL dispatch, ownership, download or loan controls. The widget
consumes a Core-produced immutable page on the GTK thread; the Core search
must run on a worker before calling this function.
"""
from __future__ import annotations

from ..core.notice_c_catalogue_views import PublicCatalogueView


def render_public_catalogue_view(view: PublicCatalogueView, *, Gtk):
    """Render one admitted catalogue page without mutating the Core result."""
    if type(view) is not PublicCatalogueView:
        raise ValueError("invalid_public_catalogue_view")
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    header = Gtk.Label(
        label=f"Catalogue public · {view.mode} · {view.known_matches} source(s) connue(s)",
    )
    header.set_xalign(0)
    root.append(header)
    for row in view.rows:
        item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(label=row.title)
        title.set_xalign(0)
        title.set_wrap(True)
        item.append(title)
        if view.mode not in ("grid", "compact"):
            if row.subtitle:
                author = Gtk.Label(label=row.subtitle)
                author.set_xalign(0)
                item.append(author)
        if view.mode in ("detail", "sources"):
            evidence = Gtk.Label(
                label=(f"Source : {row.provider_id} · notice : {row.record_id} "
                       f"· base : {row.docbase} · support : {row.medium} "
                       f"· révision : {row.source_revision or 'inconnue'} "
                       f"· édition : {row.edition_id}"),
            )
            evidence.set_xalign(0)
            evidence.set_wrap(True)
            item.append(evidence)
        if view.mode in ("detail", "sources") and row.age_observation is not None:
            for bound in row.age_observation.ranges:
                if bound.age_min is None:
                    extent = f"Jusqu’à {bound.age_max} ans"
                elif bound.age_max is None:
                    extent = f"À partir de {bound.age_min} ans"
                elif bound.age_min == bound.age_max:
                    extent = f"{bound.age_min} ans"
                else:
                    extent = f"De {bound.age_min} à {bound.age_max} ans"
                native_age = Gtk.Label(
                    label=(f"Âge déclaré ({bound.age_kind}) : {extent} "
                           f"· {bound.source_label} "
                           f"· champ : {row.age_observation.source_field}"),
                )
                native_age.set_xalign(0)
                native_age.set_wrap(True)
                item.append(native_age)
        if view.mode == "detail" and row.description:
            description = Gtk.Label(label=row.description)
            description.set_xalign(0)
            description.set_wrap(True)
            item.append(description)
        if view.mode == "timeline":
            stamp = Gtk.Label(label=f"Dernière observation : {row.last_seen_at}")
            stamp.set_xalign(0)
            item.append(stamp)
        root.append(item)
    if view.has_more or view.status != "complete":
        note = Gtk.Label(label="Résultats partiels · d'autres notices peuvent exister")
        note.set_xalign(0)
        root.append(note)
    return root
