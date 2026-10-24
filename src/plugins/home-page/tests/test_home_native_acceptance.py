"""#932: native dashboard acceptance using the public host snapshot only.

Explicit GTK/Xvfb execution; no accounts, HTTP, or Shell claims. The same
representative_rows fixture can measure the historical renderer separately.
"""
from copy import deepcopy
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

import cifereca_home as home

pytestmark = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='GTK/Xvfb display required')


def representative_rows():
    first = {
        'identity_id': 'synthetic-a', 'label': 'Profil A', 'selected': True,
        'providers': ['local', 'paris', 'kobo', 'gallica', 'gutenberg'],
        'sources': [{'provider_id': pid, 'connected': True} for pid in ('paris', 'kobo', 'gallica', 'gutenberg')],
        'statistics': {'local_books': 562, 'local_placements': 580, 'known_loans': 4,
                       'remote_owned_rights': 300,
                       'sources': {'local': 562, 'paris': 24, 'kobo': 300, 'gallica': 22, 'gutenberg': 100}},
        'sync': {'status': 'partial', 'new_acquisitions': 0, 'attention_required': 2, 'errors': 1},
        'adept_profile_id': 'synthetic-ade', 'associated_readers': [{'label': 'Liseuse témoin'}],
        'deep_last_at': 1791633600,
    }
    second = deepcopy(first)
    second.update(identity_id='synthetic-b', label='Profil B', selected=False,
                  sync={'status': 'success', 'new_acquisitions': 0, 'attention_required': 0, 'errors': 0})
    second['statistics']['known_loans'] = None
    return [first, second]


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def make_host(rows, calls, subscriptions):
    def subscribe(callback):
        subscriptions.append(callback)
        return lambda: subscriptions.remove(callback)
    return NS(identity_overview=lambda: rows, subscribe_identity_overview=subscribe,
              open_identity_library=lambda identity: calls.append(('library', identity)),
              synchronize=lambda **kw: calls.append(('sync', kw)),
              supports_identity_view_filters=lambda: True,
              open_identity_view=lambda identity, **kw: calls.append(('filter', identity, kw)),
              open_plugin_settings=lambda plugin: calls.append(('settings', plugin)))


@pytest.mark.parametrize('width', [1366, 1600])
def test_native_home_comparison_states_geometry_and_actions(width):
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, GLib, Gtk
    Adw.init()
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root / 'environnements/openai-chat-debian'))
    from x11_input import X11Input
    def pump():
        loop = GLib.MainLoop()
        GLib.timeout_add(180, lambda: (loop.quit(), False)[1])
        loop.run()
    rows = representative_rows()
    calls, subscriptions, controllers = [], [], []
    page = home.build_page(make_host(rows, calls, subscriptions), controllers)
    window = Gtk.Window(default_width=width-280, default_height=780)
    window.set_child(page)
    window.present()
    device = X11Input()
    try:
        pump()
        grid = next(node for node in walk(page) if isinstance(node, Gtk.FlowBox))
        assert isinstance(grid, Gtk.FlowBox)
        def cards():
            return [child.get_child() for child in walk(grid)
                    if isinstance(child, Gtk.FlowBoxChild) and child.get_parent() is grid]
        def texts(widget=page):
            return [node.get_text() for node in walk(widget) if isinstance(node, Gtk.Label)]
        assert len(cards()) == 2
        bounds = [card.compute_bounds(page)[1] for card in cards()]
        assert bounds[0].get_y() == bounds[1].get_y()
        assert bounds[0].get_x() + bounds[0].get_width() <= bounds[1].get_x()
        assert bounds[1].get_x() + bounds[1].get_width() <= page.get_width()
        assert all(rect.get_y() + rect.get_height() <= page.get_height() for rect in bounds)
        assert all('À traiter' in texts(card) for card in cards())
        assert sum(text.startswith('Livres locaux : 562') for text in texts()) == 2
        assert 'Local · origine locale (déjà comptée)' in texts()
        assert 'Catalogues publics' in texts() and 'Sources authentifiées' in texts()
        assert any('prêts de bibliothèque connus : —' in text for text in texts(cards()[1]))
        assert not calls  # no synchronization or settings at build
        retry = next(node for node in walk(cards()[0])
                     if isinstance(node, Gtk.Button) and node.get_label() == 'Réessayer')
        ok, rect = retry.compute_bounds(page)
        assert ok and 0 <= rect.get_y() < page.get_height()
        assert rect.get_y() + rect.get_height() <= page.get_height()
        assert retry.grab_focus()
        device.key('Tab', True); device.key('Tab', False)
        pump()
        assert window.get_focus() is not retry
        device.key('Shift_L', True)
        device.key('Tab', True); device.key('Tab', False)
        device.key('Shift_L', False)
        pump()
        assert window.get_focus() is retry
        device.click_widget(retry)
        pump()
        assert len(calls) == 1 and calls[0][0] == 'sync'
        for count in (0, 1, 3):
            rows[:] = (representative_rows() + [dict(representative_rows()[0], identity_id='synthetic-c', label='Profil C')])[:count]
            subscriptions[0]()
            pump()
            assert len(cards()) == count
            if count == 0:
                assert any('Aucune identité configurée' in text for text in texts())
        rows[:] = representative_rows()
        rows[0]['label'] = 'Profil à nom très long ' * 8
        rows[0]['statistics']['local_books'] = 1234567890
        rows[0]['sync'].update(errors=12345, attention_required=67890)
        subscriptions[0]()
        pump()
        for card in cards():
            ok, bounds = card.compute_bounds(page)
            assert ok and bounds.get_x() >= 0
            assert bounds.get_x() + bounds.get_width() <= page.get_width()
        assert any('1234567890' in text for text in texts(cards()[0]))
        assert len(calls) == 1
    finally:
        for controller in controllers:
            controller.close()
        assert not subscriptions
        window.destroy()
        device.close()
        pump()
