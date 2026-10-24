"""#763: native plugin page contracts on a local synthetic host, without HTTP.

Run explicitly under the repository's GTK/Xvfb runtime. This is a plugin
acceptance test, not a claim about remote feeds or the complete Shell.
"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import BookRef
from criticism_feeds import FeedSource
from criticism_page import CriticismPage, collect_snapshot

pytestmark = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='GTK/Xvfb display required')


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


@pytest.mark.parametrize('width', [1366, 1600])
def test_native_review_states_filters_sources_and_offline_cache(tmp_path, width):
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

    calls = []
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Rendering must not collect feeds or call a model')
    context = NS(data_directory=tmp_path, config={}, services=lambda *_a, **_k: (), broker=forbidden)
    plugin = CriticismPage().activate(context)
    book = BookRef('local-review', 'Livre témoin', ('Autrice',), identifiers={'isbn': '9780306406157'})
    host = NS(navigate=calls.append, action_context=lambda: NS(book=book), toast=calls.append)
    page = plugin.build(host)
    view = plugin.view
    window = Gtk.Window(default_width=width-40, default_height=780)
    window.set_child(page)
    window.present()
    device = X11Input()
    try:
        pump()
        def texts():
            return [node.get_text() for node in walk(page) if isinstance(node, Gtk.Label)]
        def click(button):
            assert button.get_mapped() and button.get_sensitive()
            ok, bounds = button.compute_bounds(window)
            assert ok and bounds.get_x() >= 0 and bounds.get_y() >= 0
            assert bounds.get_x() + bounds.get_width() <= window.get_width()
            assert bounds.get_y() + bounds.get_height() <= window.get_height()
            device.click_widget(button)
            pump()
        assert not view.back_button.get_visible()
        assert not view.source.get_sensitive() and not view.refresh_button.get_sensitive()
        assert not any('Aucune critique correspondante' in text for text in texts())
        click(view.choose_button)
        assert calls == ['library']
        plugin.from_book(host)
        pump()
        assert 'Aucune source de critiques active.' in ' '.join(texts())
        add = next(node for node in walk(view.results) if isinstance(node, Gtk.Button))
        click(add)
        assert view.sources_expander.get_expanded()
        assert view.url.has_focus() or window.get_focus().is_ancestor(view.url)

        feed = 'https://reviews.example/feed'
        view.plugin.store.subscribe(feed)
        view.render_sources()
        pump()
        check = next(node for node in walk(view.subscriptions) if isinstance(node, Gtk.CheckButton))
        assert not check.get_active()
        click(check)
        assert check.get_active() and view.refresh_button.get_sensitive()
        assert 'Aucune critique correspondante' in ' '.join(texts())
        sid = FeedSource(feed).source_info()['source_id']
        def article(index, isbn):
            return dict(title=f'Critique {index}', url=f'https://reviews.example/article-{index}',
                        book_title=book.title, book_author='Autrice', isbn=isbn,
                        language='fr', published_at='2026-10-08', critic_author='Critiqueur', excerpt='Extrait public')
        for count in (1, 3):
            view.plugin.store.index.ingest(sid, [article(i, '9780306406157' if i < 2 else '9780140328721') for i in range(count)])
            view.render()
            pump()
            cards = list(walk(view.results))
            links = [node for node in cards if isinstance(node, Gtk.LinkButton)]
            assert len(links) == count
            assert any(text == 'Confirmée' for text in texts())
            if count == 3:
                assert 'Suggestion à vérifier' in texts()
            for link in links:
                assert link.grab_focus()
                pump()
                assert window.get_focus() is link
                device.key('Tab', True); device.key('Tab', False)
                pump()
                assert window.get_focus() is not link
                device.key('Shift_L', True)
                device.key('Tab', True); device.key('Tab', False)
                device.key('Shift_L', False)
                pump()
                assert window.get_focus() is link
        view.since.set_text('2026-02-30')
        assert 'Date invalide' in ' '.join(texts())
        assert not any('Aucune critique correspondante' in text for text in texts())
        view.since.set_text('')
        view.language.set_text('français')
        assert 'Langue invalide' in ' '.join(texts())
        view.language.set_text('en')
        assert 'Aucune critique correspondante' in ' '.join(texts())
        reset = next(node for node in walk(view.results) if isinstance(node, Gtk.Button))
        reset.emit('clicked')
        pump()
        assert len([node for node in walk(view.results) if isinstance(node, Gtk.LinkButton)]) == 3
        # Failed collection is explicit, and the existing cached reviews survive.
        def offline(_url):
            raise OSError('synthetic offline')
        result = collect_snapshot([FeedSource(feed, fetch=offline)], NS(cancelled=lambda: False))
        view.collected(result)
        pump()
        assert '1 source(s) en échec' in view.status.get_text()
        assert len([node for node in walk(view.results) if isinstance(node, Gtk.LinkButton)]) == 3
        view.render_sources()
        pump()
        check = next(node for node in walk(view.subscriptions) if isinstance(node, Gtk.CheckButton))
        click(check)
        assert not check.get_active()
        assert 'Aucune source de critiques active.' in ' '.join(texts())
        assert not view.refresh_button.get_sensitive()
        assert calls == ['library', 'literary-criticism']
    finally:
        plugin.deactivate()
        window.destroy()
        device.close()
        pump()
