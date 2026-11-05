"""Refresh indexed search state from SQLite, never from a provider.

Called only after successful materialization on the GTK completion thread.
Window/search generations own delivery; one pending read and one dirty bit
coalesce acquisitions without rendering an older snapshot.
"""
from __future__ import annotations

from ..task_queue import TaskPriority


def _identity(window):
    return getattr(getattr(window.service, 'current_identity', None), 'identity_id', None)


def _refs(row, owner):
    """Exact existing source-right identities, not bibliographic aliases.

    The provider can issue the same edition to distinct credentials or
    borrowers. Matching only provider/item/identity could redirect a selected
    remote book to another borrower's row during a refreshed search.
    """
    if type(row) is not dict:
        return set()
    possessions = row.get('possessions')
    if type(possessions) not in (list, tuple):
        return set()
    return {
        (p['entitlement_id'], p['provider_id'], p['item_id'],
         p.get('account_id'), owner)
        for p in possessions
        if type(p) is dict and p.get('identity_id') == owner
        and all(type(p.get(k)) is str and p[k]
                for k in ('entitlement_id', 'provider_id', 'item_id'))
        and (p.get('account_id') is None or type(p.get('account_id')) is str)
    }


def _selected_key(window, rows, owner):
    selected = window.search_page.selection.get_selected_item()
    key = getattr(selected, 'key', None)
    if any(str(row.get('key')) == key for row in rows):
        return key
    previous = getattr(window, '_search_local_objects', {}).get(key, {})
    refs = _refs(previous, owner)
    if not refs:
        return None
    # A previously grouped multi-account row may split on refreshed
    # provenance. Do not guess which of its former rights was selected.
    matches = [
        str(row['key']) for row in rows
        if refs.issubset(_refs(row, owner))
    ]
    return matches[0] if len(matches) == 1 else None


def invalidate_search_snapshot(window):
    if getattr(window, '_closed', False) or not hasattr(window, 'search_controls'):
        return
    window._search_local_snapshot_dirty = True
    # Online rows stay authoritative. The next explicit render reads fresh
    # local placements and replaces same-key widgets using this dirty bit.
    online_pending = (getattr(window, '_online_pending_key', None) is not None
                      and getattr(window, '_online_pending_generation', None)
                      == getattr(window, '_search_generation', -1))
    if getattr(window, '_search_mode', None) != 'local' or online_pending:
        return
    state = window.search_controls.state()
    if len(state.text) < 2 or not hasattr(window, 'search_page'):
        return
    if getattr(window, '_search_snapshot_pending', None) is not None:
        window._search_snapshot_again = True
        return
    owner = _identity(window)
    if owner is None:
        return
    identity_generation = getattr(window, '_identity_context_generation', None)
    window._search_generation = getattr(window, '_search_generation', 0) + 1
    search_generation = window._search_generation
    query = (state.text, state.field, state.scope)
    token = object()
    window._search_snapshot_pending = token
    window._search_snapshot_again = False

    def identity_current():
        return (not getattr(window, '_closed', False) and owner == _identity(window)
                and identity_generation == getattr(window, '_identity_context_generation', None))

    def current():
        if not identity_current() or window._search_mode != 'local':
            return False
        live = window.search_controls.state()
        return (search_generation == window._search_generation
                and query == (live.text, live.field, live.scope))

    def work():
        if (not identity_current() or search_generation != window._search_generation
                or window._search_mode != 'local'):
            return []
        return window.service.search_known_catalog(
            state.text, state.scope, field=state.field, identity_id=owner)

    def finish():
        if getattr(window, '_search_snapshot_pending', None) is not token:
            return False
        window._search_snapshot_pending = None
        again = window._search_snapshot_again
        window._search_snapshot_again = False
        if again and identity_current():
            invalidate_search_snapshot(window)
            return False
        return current()

    def done(rows):
        if not finish():
            return
        key = _selected_key(window, rows, owner)
        window._populate_local_search(rows)
        # Core can replace source:<provider>:<purchase> with artifact:<sha>.
        # Preserve selection only via unique, exact same-owner source rights.
        page = window.search_page
        if key is not None:
            for index in range(page.store.get_n_items()):
                if page.store.get_item(index).key == key:
                    page.selection.set_selected(index)
                    break

    def failed(_exc):
        # Keep the previous view, with no private provider/path error echoed.
        finish()

    window.runner.submit('Actualisation de la recherche locale…', work, done, failed,
                         priority=TaskPriority.NORMAL, kind='search-known',
                         origin='automatic')
