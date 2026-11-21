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
_LINK_PREFIX = "maintenance.link."
_UNAVAILABLE_LABELS = {
    "maintenance_safety_hold": "suspendu — garde de sécurité",
    "missing_broker": "indisponible — service requis absent",
    "missing_service": "indisponible — service requis absent",
    "contract_mismatch": "indisponible — contrat incompatible",
    "configuration_missing": "indisponible — configuration requise",
    "binary_missing": "indisponible — dépendance système absente",
    "python_dependency_missing": "indisponible — dépendance système absente",
    "system_resource_missing": "indisponible — dépendance système absente",
}


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
        if (type(title) is not str or not 0 < len(title) <= 256
                or not title.isprintable()):
            title = "Outil de maintenance"
        code = getattr(status, "reason_code", None)
        state = getattr(status, "enabled", None)
        suffix = _UNAVAILABLE_LABELS.get(code) if type(code) is str else None
        if suffix is None:
            suffix = "désactivé" if state is False else "indisponible"
        label = Gtk.Label(label=title + " — " + suffix, xalign=0, wrap=True)
        label.add_css_class("dim-label")
        sections[presentation_group(d.capabilities)].append(label)


def add_section_links(sections, services, *, Gtk, current):
    """Declared cross-navigation only; no alternate execution entry point."""
    names = dict(GROUPS)
    links = set()
    for service in services:
        caps = getattr(service, "capabilities", ())
        group = presentation_group(caps)
        if group not in names:
            continue
        for cap in caps:
            if type(cap) is not str or not cap.startswith(_LINK_PREFIX):
                continue
            target = cap[len(_LINK_PREFIX):]
            if target in names and target != group:
                links.add((group, target))
    for group, target in sorted(links):
        heading = sections[target].get_first_child()
        if heading is None:
            continue
        heading.set_focusable(True)
        button = Gtk.Button(label="Voir : " + names[target])
        button.set_tooltip_text("Aller au parcours indiqué, sans lancer d’opération.")

        def navigate(control, destination=heading):
            if (current() is True and control.get_root() is not None
                    and destination.get_root() is control.get_root()):
                destination.grab_focus()

        button.connect("clicked", navigate)
        sections[group].append(button)


def finish_sections(sections, *, Gtk):
    for key, container in sections.items():
        if container.get_first_child() is container.get_last_child():
            if key == _FALLBACK:
                container.set_visible(False)
            else:
                label = Gtk.Label(label="Aucune contribution disponible.", xalign=0, wrap=True)
                label.add_css_class("dim-label")
                container.append(label)
