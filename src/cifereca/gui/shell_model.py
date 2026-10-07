"""GTK-independent page/action resolution shared by the desktop shell."""
from dataclasses import dataclass

from cifereca.plugin_api import UiActionContribution, UiPageContribution


@dataclass(frozen=True, slots=True)
class RegisteredAction:
    key: str
    page_route: str
    page_title: str
    title: str
    scope: str
    action: UiActionContribution


def resolve_pages(pages):
    resolved = {}
    for page in pages:
        if not isinstance(page, UiPageContribution) or not page.route or not callable(page.factory):
            continue
        current = resolved.get(page.route)
        if current is None or page.priority > current.priority:
            resolved[page.route] = page
    return sorted(resolved.values(), key=lambda p: ({'usage': 0, 'configuration': 1}.get(p.group, 2), p.group, p.order, p.route))


def page_actions(page):
    implicit = UiActionContribution('open', f'Afficher {page.title}',
                                    lambda host: host.navigate(page.route), page.icon_name, True)
    return (implicit,) + tuple(a for a in (page.actions or ()) if isinstance(a, UiActionContribution) and isinstance(a.id, str) and a.id and a.id != 'open' and callable(a.callback) and a.target in {'page', 'header'})


def action_registry(pages):
    """Return one deterministic registry row per declarative page action."""
    rows = []
    seen = set()
    for page in resolve_pages(pages):
        for action in page_actions(page):
            key = f'{page.route}.{action.id}'
            if key in seen:
                continue
            seen.add(key)
            rows.append(RegisteredAction(
                key=key,
                page_route=page.route,
                page_title=page.title,
                title=action.title,
                scope='global' if action.global_action else 'page',
                action=action,
            ))
    return tuple(rows)


def validate_action_selection(pages, selected):
    """Split configured ids into valid and obsolete entries, preserving order."""
    known = {row.key for row in action_registry(pages)}
    valid = []
    obsolete = []
    for raw in selected or ():
        key = str(raw or '').strip()
        if not key or key in valid or key in obsolete:
            continue
        (valid if key in known else obsolete).append(key)
    return tuple(valid), tuple(obsolete)


def visible_actions(pages, route, selected):
    valid, _obsolete = validate_action_selection(pages, selected)
    selected_keys = set(valid)
    return [(row.key, row.action) for row in action_registry(pages)
            if row.key in selected_keys and (row.scope == 'global' or row.page_route == route)]


def delegated_accounts(accounts, identity_id, public_providers=()):
    """Primary context plus delegated borrowers, never public catalogues."""
    private = [a for a in accounts if a.identity_id == identity_id and a.provider_id not in public_providers]
    if not any(not a.is_primary for a in private):
        return []
    return private


# Native menu sections are shared with the shell; stable order, no GTK imports.
def menu_sections(pages, route):
    groups = {}
    context = []
    for page in resolve_pages(pages):
        actions = page_actions(page)
        groups.setdefault(page.group, []).append((page.title, actions[0]))
        context.extend((action.title, action) for action in actions[1:]
                       if action.global_action or page.route == route)
    result = [({'usage': 'Pages', 'configuration': 'Configuration / Technique'}.get(group, group), entries)
              for group, entries in groups.items()]
    if context:
        result.append(('Actions', context))
    return result


def contextual_actions(pages, target, context):
    """Return public action contributions applicable to a contextual target.

    Contextual actions are declared on any ui.page contribution but are not
    rendered as page/header actions.  This keeps the extension point additive
    without introducing a second plugin category solely for buttons.
    """
    rows = []
    for page in pages:
        for action in getattr(page, "actions", ()) or ():
            if not isinstance(action, UiActionContribution) or action.target != target or not callable(action.callback):
                continue
            try:
                if action.visible_when is not None and not action.visible_when(context):
                    continue
            except Exception:
                continue
            enabled = True
            try:
                if action.enabled_when is not None:
                    enabled = bool(action.enabled_when(context))
            except Exception:
                enabled = False
            rows.append((page.route, action, enabled))
    rows.sort(key=lambda row: (row[0], row[1].id))
    return tuple(rows)
