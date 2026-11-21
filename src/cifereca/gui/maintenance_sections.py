"""#7407: declarative presentation only, no registry or execution authority."""
GROUPS = (
    ("organization", "Organiser la bibliothèque"),
    ("integrity", "Vérifier l’intégrité"),
    ("quality", "Qualité documentaire"),
    ("provenance", "Origine et provenance"),
    ("recovery", "Reprise des opérations"),
    ("storage", "Stockage et entretien technique"),
)
_PREFIX = "maintenance.group."
_FALLBACK = "unclassified"


def presentation_group(capabilities):
    """Exactly one known group, otherwise explicit compatibility fallback."""
    try:
        declared = {value[len(_PREFIX):] for value in capabilities
                    if type(value) is str and value.startswith(_PREFIX)}
    except TypeError:
        return _FALLBACK
    if len(declared) != 1:
        return _FALLBACK
    value = next(iter(declared))
    return value if value in dict(GROUPS) else _FALLBACK


def build_sections(box, *, Gtk):
    sections = {}
    for key, name in (*GROUPS, (_FALLBACK, "Autres contributions — non classées")):
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Label(label=name, xalign=0, wrap=True)
        heading.add_css_class("heading")
        container.append(heading)
        box.append(container)
        sections[key] = container
    return sections


def show_unavailable(sections, statuses, services, *, Gtk):
    """Registry observations only. Never expose exception text or enable tools."""
    active = {row.plugin_id for row in services}
    for status in statuses:
        d = status.descriptor
        if d.type != "maintenance.tool" or d.id in active:
            continue
        title = getattr(d, "display_name", None) or d.id
        if type(title) is not str or not 0 < len(title) <= 256:
            title = "Outil de maintenance"
        label = Gtk.Label(label=title + " — indisponible ou désactivé", xalign=0, wrap=True)
        label.add_css_class("dim-label")
        sections[presentation_group(d.capabilities)].append(label)


def finish_sections(sections, *, Gtk):
    for key, container in sections.items():
        if container.get_first_child() is container.get_last_child():
            if key == _FALLBACK:
                container.set_visible(False)
            else:
                label = Gtk.Label(label="Aucune contribution disponible.", xalign=0, wrap=True)
                label.add_css_class("dim-label")
                container.append(label)
