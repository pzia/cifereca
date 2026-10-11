"""Constellations page and contextual action, using public Plugin API only."""
from cifereca.plugin_api import (UiPageContribution, UiActionContribution,
    UiBookDetailPresentation, DetailSection, DetailField, DocumentLink)
from constellation_view_model import (node_from_row, merge_nodes, neighbours, Navigation)


def neighbour_card_fields(edge):
    """Read-only presentation, with inferred reasons kept distinguishable."""
    book = edge.target.book
    reasons = tuple(str(item) for item in edge.reasons if str(item))
    certain = tuple(value for value in reasons if not value.startswith('Inféré :'))
    inferred = tuple(value for value in reasons if value.startswith('Inféré :'))
    return {
        'title': str(book.title or ''),
        'authors': ', '.join(str(value) for value in book.authors if str(value)),
        'access': ', '.join(str(value) for value in edge.target.availability if str(value)),
        'reason': certain[0] if certain else (inferred[0] if inferred else 'Raison inconnue'),
        'other_reasons': max(0, len(reasons) - 1),
        'inferred': bool(inferred),
    }


def neighbour_count_label(edges):
    number = len(edges)
    inferred = sum(any(str(item).startswith('Inféré :') for item in e.reasons) for e in edges)
    return (f"{number} voisin(s) proposé(s) · {inferred} avec un indice inféré"
            if number else "Aucun voisin proposé avec les paramètres actuels")


class ConstellationsPlugin:
    def activate(self, context):
        self.context, self.view, self.pending = context, None, None
        self.pending_identity = ""
        return self

    def pages(self):
        return [UiPageContribution('constellations', 'Constellations', 'view-grid-symbolic', 36,
            self.build, actions=(UiActionContribution('constellations.explore', 'Explorer les livres proches',
            self.from_book, target='book.detail', visible_when=lambda c: c.book is not None),))]

    def from_book(self, host):
        action = host.action_context()
        self.pending = action.book
        self.pending_identity = str(getattr(action, 'identity_id', '') or '')
        if self.view:
            self.view.seed(self.pending, identity_id=self.pending_identity)
        host.navigate('constellations')

    def build(self, host):
        from gi.repository import Gtk, GLib
        self.view = View(self, host, Gtk, GLib)
        return self.view.widget

    def deactivate(self):
        if self.view:
            self.view.dispose()
        self.view = self.pending = None
        self.pending_identity = ""


class View:
    def __init__(self, plugin, host, Gtk, GLib):
        self.plugin, self.host, self.Gtk, self.GLib = plugin, host, Gtk, GLib
        self.nodes, self.local, self.cache = (), (), {}
        self.nav, self.center = Navigation(), None
        self.origin_book = plugin.pending
        self.origin_identity = plugin.pending_identity
        self.generation, self.task, self.disposed = 0, None, False
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.status = Gtk.Label(label='Choisissez un livre dans Recherche ou Bibliothèque pour explorer ses proches.', wrap=True, xalign=0)
        self.widget.append(self.status)
        self.identities = list(plugin.context.broker('identities').identities())
        self.identity = Gtk.DropDown.new_from_strings([str(r.get('label') or r.get('name') or r.get('identity_id')) for r in self.identities])
        self.identity.set_tooltip_text('Profil de bibliothèque')
        if self.origin_identity:
            wanted = next((i for i, row in enumerate(self.identities)
                           if str(row.get('identity_id') or '') == self.origin_identity), None)
            if wanted is not None:
                self.identity.set_selected(wanted)
        self.widget.append(self.identity)
        self.identity.connect('notify::selected', lambda *_: self.load())
        # MVP: a known book -> explained neighbours -> previous/next book.
        # No free search, no manual start/end, no mood/exploration controls.
        self.controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.widget.append(self.controls)
        self.back_button = self.button('← Livre précédent', lambda: self.move(-1))
        self.forward_button = self.button('Livre suivant →', lambda: self.move(1))
        self.origin_button = self.button('Retour à la fiche d’origine', self.open_origin)
        for button in (self.back_button, self.forward_button, self.origin_button):
            self.controls.append(button)
        self.promise = Gtk.Label(
            label='Découvrez des livres liés au livre choisi. Cliquez un voisin pour continuer.',
            wrap=True, xalign=0,
        )
        self.widget.append(self.promise)
        self.heading = Gtk.Label(wrap=True, xalign=0)
        self.widget.append(self.heading)
        self.master = host.build_book_master_detail(title='Livre central', empty_title='Aucun livre sélectionné dans cette liste')
        self.widget.append(self.master.widget)
        self.neighbours_summary = Gtk.Label(label='Aucun voisin proposé', wrap=True, xalign=0)
        self.widget.append(self.neighbours_summary)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=1)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        scroll.set_child(self.flow)
        self.widget.append(scroll)
        self.unsubscribe = host.subscribe_identity_overview(lambda: GLib.idle_add(self.invalidate)) if hasattr(host, 'subscribe_identity_overview') else lambda: None
        self.widget.connect('map', self.first_map)
        self.started = False

    def first_map(self, *_):
        if not self.started:
            self.started = True
            self.load()

    def button(self, text, callback):
        b = self.Gtk.Button(label=text)
        b.connect('clicked', lambda *_: callback())
        return b

    def identity_id(self):
        i = self.identity.get_selected()
        return str(self.identities[i].get('identity_id') or '') if i < len(self.identities) else ''

    def invalidate(self):
        if self.disposed:
            return False
        self.cache.clear()
        current = self.identity_id()
        rows = list(self.plugin.context.broker('identities').identities())
        if rows != self.identities:
            self.identities = rows
            self.identity.set_model(self.Gtk.StringList.new([str(r.get('label') or r.get('name') or r.get('identity_id')) for r in rows]))
            selected = next((i for i,r in enumerate(rows) if str(r.get('identity_id')) == current), 0)
            self.identity.set_selected(selected)
        self.load(refresh=True)
        return False

    def load(self, *, remote='', refresh=False):
        identity = self.identity_id()
        if not identity:
            self.generation += 1
            if self.task:
                self.task.cancel()
            self.nodes, self.local, self.center = (), (), None
            self.render()
            self.status.set_label('Aucun profil disponible. Créez un profil dans Cifereca.')
            return
        self.nodes, self.local, self.center = (), (), None
        self.nav = Navigation()
        self.render()
        cached = None if refresh else self.cache.get(identity)
        pending = self.plugin.pending
        context = self.plugin.context
        self.status.set_label('Chargement…')
        def operation(control):
            rows = cached if cached is not None else tuple(context.broker('metadata').library_entries(identity_id=identity, scan=False))
            local = tuple(node_from_row(r, origin='bibliothèque') for r in rows[:300])
            extra = ()
            if remote and not control.cancelled():
                extra = tuple(node_from_row(r) for r in context.broker('catalog').search(remote, identity_id=identity, limit=100)[:100])
            if control.cancelled():
                return None
            seed = node_from_row(pending, origin='sélection') if pending else None
            nodes = merge_nodes(local + extra + ((seed,) if seed else ()))
            return rows, local, nodes, seed.key if seed else None
        self.run_calculation(operation, lambda result: self.loaded(self.generation, identity, result, ''), 'Chargement Constellations')

    def run_calculation(self, operation, complete, label):
        """Run CPU/network work outside GTK; only current-context results apply."""
        self.generation += 1
        generation, identity = self.generation, self.identity_id()
        if self.task:
            self.task.cancel()
        self.task = self.plugin.context.broker('tasks').submit(operation, label=label, kind='constellations')
        def done(handle):
            if handle.control.cancelled():
                return
            try:
                result, error = handle.result(), False
            except Exception:
                result, error = None, True
            def apply():
                if self.disposed or generation != self.generation or identity != self.identity_id():
                    return False
                if error:
                    self.status.set_label('Calcul impossible. Les résultats précédents sont conservés.')
                elif result is not None:
                    complete(result)
                return False
            self.GLib.idle_add(apply)
        self.task.add_done_callback(done)

    def loaded(self, generation, identity, result, error):
        if self.disposed or generation != self.generation:
            return False
        if error:
            self.status.set_label(error); return False
        rows, self.local, self.nodes, seed_key = result
        self.cache[identity] = rows
        self.status.set_label(
            f'{len(self.nodes)} notices connues pour ce profil · voisinage local sans collecte automatique'
        )
        self.plugin.pending = None
        if seed_key:
            seed = next((n for n in self.nodes if n.key == seed_key), None)
            if seed is not None:
                self.visit(seed)
            else:
                self.render()
        else:
            # Do not select an arbitrary first record on direct page access.
            self.render()
        return False

    def seed(self, book, *, identity_id=''):
        if book is None:
            return
        self.origin_book = book
        self.origin_identity = identity_id or self.identity_id()
        if identity_id and identity_id != self.identity_id():
            selected = next((i for i, row in enumerate(self.identities)
                             if str(row.get('identity_id') or '') == identity_id), None)
            if selected is not None:
                self.plugin.pending = book
                self.identity.set_selected(selected)
                return
        if not self.nodes:
            self.plugin.pending = book
            self.load()
            return
        self.plugin.pending = None
        node = node_from_row(book, origin='sélection')
        current = next((n for n in self.nodes if n.key == node.key), None)
        if current:
            self.visit(current)
            return
        snapshot = self.nodes
        def apply(nodes):
            self.nodes = nodes
            self.visit(next(n for n in nodes if n.key == node.key))
        self.status.set_label('Ajout du livre central…')
        self.run_calculation(lambda control: merge_nodes(snapshot + (node,)), apply, 'Calcul du voisinage')

    def cancel_calculation(self):
        self.generation += 1
        if self.task:
            self.task.cancel()

    def visit(self, node):
        self.cancel_calculation()
        self.center = node; self.nav.visit(node.key); self.render()

    def move(self, offset):
        self.cancel_calculation()
        key = self.nav.move(offset)
        self.center = next((n for n in self.nodes if n.key == key), self.center)
        self.render()

    def open_book(self):
        method = getattr(self.host, 'open_book_detail', None)
        if self.center and callable(method) and method(self.center.book, identity_id=self.identity_id()):
            return
        self.host.toast('Fiche locale indisponible pour ce profil ; la notice reste affichée ici.')

    def open_origin(self):
        """Return through public book-detail hosting with the original book."""
        original = getattr(self, 'origin_book', None)
        if original is None:
            self.host.toast('Sélectionnez d’abord un livre dans Recherche ou Bibliothèque.')
            return
        method = getattr(self.host, 'open_book_detail', None)
        if callable(method) and method(original, identity_id=self.origin_identity or self.identity_id()):
            return
        self.host.toast('Fiche d’origine indisponible pour ce profil.')

    def render(self):
        if not hasattr(self, 'flow'):
            return
        child = self.flow.get_first_child()
        while child:
            self.flow.remove(child); child = self.flow.get_first_child()
        back = getattr(self, 'back_button', None)
        forward = getattr(self, 'forward_button', None)
        origin = getattr(self, 'origin_button', None)
        if back is not None:
            back.set_sensitive(self.nav.position > 0)
        if forward is not None:
            forward.set_sensitive(0 <= self.nav.position < len(self.nav.history) - 1)
        if origin is not None:
            origin.set_sensitive(getattr(self, 'origin_book', None) is not None)
        if not self.center:
            self.heading.set_label('Choisissez un livre dans Recherche ou Bibliothèque')
            self.neighbours_summary.set_label('Aucun point de départ. Utilisez « Explorer les livres proches » depuis une fiche.')
            self.master.empty_detail(); return
        node = self.center
        self.heading.set_label('Centre : ' + node.book.title)
        links = (DocumentLink('notice', 'Voir la notice en ligne', node.url),) if node.url else ()
        self.master.show_detail(UiBookDetailPresentation(node.book.title, ', '.join(node.book.authors),
            badges=node.availability, links=links, sections=(DetailSection('provenance', 'Provenance',
            (DetailField('sources', 'Sources', ', '.join(node.sources)),)),)))
        # A single stable ranking keeps the first exploration comprehensible.
        edges = neighbours(self.nodes, node, mode='Confort', surprise=.25, profile=self.local, limit=12)
        self.neighbours_summary.set_label(neighbour_count_label(edges))
        if not edges:
            self.flow.append(self.Gtk.Label(
                label='Aucun livre proche documenté dans le cache actuel. Revenez à la fiche d’origine ou choisissez une autre fiche.',
                wrap=True, xalign=0,
            ))
        for edge in edges:
            row = neighbour_card_fields(edge)
            card = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=3)
            card.append(self.Gtk.Label(label=row['title'], wrap=True, xalign=0))
            if row['authors']:
                card.append(self.Gtk.Label(label='Auteur : ' + row['authors'], wrap=True, xalign=0))
            card.append(self.Gtk.Label(label='Lien principal : ' + row['reason'], wrap=True, xalign=0))
            if row['other_reasons']:
                card.append(self.Gtk.Label(
                    label=f"{row['other_reasons']} autre(s) raison(s)", wrap=True, xalign=0,
                ))
            card.append(self.Gtk.Label(
                label='Accès : ' + (row['access'] or 'non précisé'), wrap=True, xalign=0,
            ))
            if row['inferred']:
                card.append(self.Gtk.Label(label='Contient un indice inféré', wrap=True, xalign=0))
            card.append(self.Gtk.Label(label='Visiter ce livre', xalign=0))
            button = self.Gtk.Button()
            button.set_child(card)
            button.set_tooltip_text('Visiter : ' + row['title'])
            button.connect('clicked', lambda *_args, node=edge.target: self.visit(node))
            self.flow.append(button)

    def dispose(self):
        self.disposed = True; self.generation += 1
        if self.task:
            self.task.cancel()
        self.unsubscribe(); self.cache.clear()


PLUGIN = ConstellationsPlugin()
