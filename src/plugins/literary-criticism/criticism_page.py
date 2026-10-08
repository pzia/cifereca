"""Book-context review page. Merely opening it reads the local cache only."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from cifereca.plugin_api import UiPageContribution, UiActionContribution
from literary_criticism import CriticismIndex, LiteraryCriticismPlugin, canonical_url, source_info
from criticism_feeds import FeedSource, discover_url, date_text


def filtered_matches(index, book, *, source='', language='', since=''):
    if book is None:
        return ()
    cutoff = date_text(since) if since else ''
    return tuple(m for m in index.matches_for(book)
        if (not source or source.casefold() in ' '.join(m.article.provenance).casefold())
        and (not language or m.article.language.casefold() == language.casefold())
        and (not since or (cutoff and date_text(m.article.published_at) and date_text(m.article.published_at) >= cutoff)))


class ReviewStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'reviews-v1.json'
        self.subscriptions, self.index, self.freshness = {}, CriticismIndex(), {}
        try:
            if self.path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Cache trop volumineux')
            raw = json.loads(self.path.read_text())
            self.subscriptions = {url: enabled is True for url, enabled in raw.get('subscriptions', {}).items() if canonical_url(url) == url}
            for sid, rows in raw.get('sources', {}).items():
                self.index.ingest(sid, rows[:500])
            self.freshness = raw.get('freshness', {})
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        sources = {}
        for article in self.index.articles():
            for sid in article.provenance:
                sources.setdefault(sid, []).append(asdict(article))
        payload = {'subscriptions': self.subscriptions, 'sources': sources, 'freshness': self.freshness}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        temporary.replace(self.path)

    def subscribe(self, url):
        url = canonical_url(url)
        if not url:
            raise ValueError('URL invalide')
        self.subscriptions.setdefault(url, False)
        self.save()

    def sources(self, context):
        sources = [FeedSource(url) for url, enabled in self.subscriptions.items() if enabled]
        sources.extend(row.service for row in context.services('criticism.source', contract='cifereca.criticism-source', version='>=1,<2'))
        return sources


def collect_snapshot(sources, control):
    """Worker owns its index. A discarded completion cannot mutate visible state."""
    service, reports, seen = LiteraryCriticismPlugin(), [], set()
    for source in sources:
        if control.cancelled():
            break
        try:
            sid = source_info(source).source_id
        except Exception:
            reports.append(('source', 'Source incompatible', ''))
            continue
        if sid in seen:
            continue
        seen.add(sid)
        cursor, cursors = None, set()
        for _ in range(5):
            if control.cancelled():
                break
            report = service.collect_source(source, cursor=cursor, limit=100)
            reports.append((sid, 'Collecte incomplète' if report.errors else '', report.fresh_until))
            if report.errors or not report.next_cursor or report.next_cursor in cursors:
                break
            cursor = report.next_cursor; cursors.add(cursor)
    return service.index, reports


class CriticismPage:
    def activate(self, context):
        self.context, self.view, self.pending = context, None, None
        self.store = ReviewStore(context.data_directory)
        return self

    def pages(self):
        return [UiPageContribution('literary-criticism', 'Critiques littéraires', 'accessories-dictionary-symbolic', 37,
            self.build, actions=(UiActionContribution('criticism.book', 'Consulter les critiques', self.from_book,
            target='book.detail', visible_when=lambda c: c.book is not None),))]

    def from_book(self, host):
        self.pending = host.action_context().book
        if self.view:
            self.view.book = self.pending
            self.view.render()
        host.navigate('literary-criticism')

    def build(self, host):
        from gi.repository import Gtk, GLib
        self.view = ReviewView(self, host, Gtk, GLib)
        return self.view.widget

    def deactivate(self):
        if self.view:
            self.view.dispose()
        self.view, self.pending = None, None


class ReviewView:
    def __init__(self, plugin, host, Gtk, GLib):
        self.plugin, self.host, self.Gtk, self.GLib = plugin, host, Gtk, GLib
        self.book = plugin.pending
        self.generation, self.task, self.disposed = 0, None, False
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.heading = Gtk.Label(wrap=True, xalign=0)
        self.widget.append(self.heading)
        self.status = Gtk.Label(label='Cache local uniquement. Actualisez explicitement pour consulter les sources.', wrap=True, xalign=0)
        self.widget.append(self.status)
        controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        self.widget.append(controls)
        self.source = Gtk.Entry(placeholder_text='Filtrer par source')
        self.language = Gtk.Entry(placeholder_text='Langue (fr, en…)')
        self.since = Gtk.Entry(placeholder_text='Depuis AAAA-MM-JJ')
        for entry in (self.source, self.language, self.since):
            controls.append(entry); entry.connect('changed', lambda *_: self.render())
        controls.append(self.button('Actualiser les sources actives', self.refresh))
        controls.append(self.button('Retour à la fiche du livre', self.open_book))
        self.url = Gtk.Entry(placeholder_text='URL de page ou de flux RSS/Atom/JSON Feed', hexpand=True)
        self.widget.append(self.url)
        self.widget.append(self.button('Prévisualiser les flux', self.preview))
        self.previews = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(self.previews)
        self.subscriptions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(self.subscriptions)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll = Gtk.ScrolledWindow(vexpand=True); scroll.set_child(self.results)
        self.widget.append(scroll)
        self.render_sources(); self.render()

    def button(self, label, callback):
        b = self.Gtk.Button(label=label); b.connect('clicked', lambda *_: callback()); return b

    def clear(self, box):
        child = box.get_first_child()
        while child:
            box.remove(child); child = box.get_first_child()

    def run(self, operation, complete):
        self.generation += 1; generation = self.generation
        if self.task:
            self.task.cancel()
        self.status.set_label('Chargement demandé…')
        self.task = self.plugin.context.broker('tasks').submit(operation, label='Critiques littéraires', kind='criticism')
        def done(handle):
            try:
                result, error = handle.result(), False
            except Exception:
                result, error = None, True
            def apply():
                if not self.disposed and generation == self.generation:
                    if error:
                        self.status.set_label('Échec de la source. Le cache précédent est conservé.')
                    else:
                        complete(result)
                return False
            self.GLib.idle_add(apply)
        self.task.add_done_callback(done)

    def preview(self):
        url = canonical_url(self.url.get_text())
        if not url:
            self.status.set_label('Saisissez une URL HTTP(S).'); return
        self.run(lambda control: discover_url(url), self.show_previews)

    def show_previews(self, candidates):
        self.clear(self.previews)
        self.status.set_label(f'{len(candidates)} flux détecté(s). Aucun abonnement activé automatiquement.')
        for candidate in candidates:
            self.previews.append(self.button('Ajouter (désactivé) : ' + candidate.url, lambda url=candidate.url: self.add(url)))

    def add(self, url):
        self.plugin.store.subscribe(url); self.render_sources()

    def render_sources(self):
        self.clear(self.subscriptions)
        for url, enabled in self.plugin.store.subscriptions.items():
            check = self.Gtk.CheckButton(label=url, active=enabled)
            check.connect('toggled', lambda button, u=url: self.toggle(u, button.get_active()))
            self.subscriptions.append(check)
        self.subscriptions.append(self.Gtk.Label(label='Les plugins de source activés dans le gestionnaire sont également consultés.', wrap=True, xalign=0))

    def toggle(self, url, enabled):
        self.plugin.store.subscriptions[url] = enabled
        self.plugin.store.save()
        # Drop in-flight results obtained under the old subscription selection.
        self.generation += 1
        if self.task:
            self.task.cancel()
        self.render()

    def refresh(self):
        sources = self.plugin.store.sources(self.plugin.context)
        if not sources:
            self.status.set_label('Ajoutez puis activez une source avant d’actualiser.'); return
        self.run(lambda control: collect_snapshot(sources, control), self.collected)

    def collected(self, result):
        index, reports = result
        # Retain old articles only for failed sources; never mix a stale successful feed.
        failed = {sid for sid, error, _ in reports if error}
        for article in self.plugin.store.index.articles():
            for sid in article.provenance:
                if sid in failed:
                    index.ingest(sid, [asdict(article)])
        self.plugin.store.index = index
        self.plugin.store.freshness.update({sid: fresh for sid, error, fresh in reports if not error})
        self.plugin.store.save()
        self.status.set_label(f'{len(index.articles())} critiques en cache · {len(failed)} source(s) en échec')
        self.render()

    def open_book(self):
        method = getattr(self.host, 'open_book_detail', None)
        if self.book and callable(method) and method(self.book):
            return
        self.host.toast('La fiche locale n’est pas disponible dans le profil actif.')

    def render(self):
        self.heading.set_label('Critiques : ' + self.book.title if self.book else 'Depuis une fiche livre, choisissez « Consulter les critiques ».')
        self.clear(self.results)
        disabled = {FeedSource(url).source_info()['source_id'] for url, enabled in self.plugin.store.subscriptions.items() if not enabled}
        matches = filtered_matches(self.plugin.store.index, self.book, source=self.source.get_text().strip(),
            language=self.language.get_text().strip(), since=self.since.get_text().strip())
        matches = tuple(m for m in matches if set(m.article.provenance) - disabled)
        if not matches:
            self.results.append(self.Gtk.Label(label='Aucune critique associée dans le cache pour ces filtres.', wrap=True))
        for match in matches:
            article = match.article
            status = 'Rattachement confirmé' if match.status == 'confirmed' else 'Suggestion à vérifier'
            details = '\n'.join([article.title, status + ' · ' + ', '.join(match.reasons),
                'Sources : ' + ', '.join(article.provenance),
                'Fraîcheur : ' + ', '.join(sid + ' — ' + ('à actualiser' if not self.plugin.store.freshness.get(sid) or self.plugin.store.freshness[sid] < datetime.now(timezone.utc).isoformat() else 'en cache récent') for sid in article.provenance),
                ' · '.join(filter(None, [article.critic_author, article.published_at, article.language])), article.excerpt])
            self.results.append(self.Gtk.Label(label=details, wrap=True, xalign=0, selectable=True))
            self.results.append(self.Gtk.LinkButton.new_with_label(article.url, 'Lire la critique dans le navigateur'))

    def dispose(self):
        self.disposed = True; self.generation += 1
        if self.task:
            self.task.cancel()


PLUGIN = CriticismPage()
