"""Constellations page and contextual action, using public Plugin API only."""
from cifereca.plugin_api import (UiPageContribution, UiActionContribution,
    UiBookDetailPresentation, DetailSection, DetailField, DocumentLink)
from constellation_view_model import (node_from_row, merge_nodes, neighbours, relation,
    promenade, replace_step, Navigation)


class ConstellationsPlugin:
    def activate(self, context):
        self.context, self.view, self.pending = context, None, None
        return self

    def pages(self):
        return [UiPageContribution('constellations', 'Constellations', 'view-grid-symbolic', 36,
            self.build, actions=(UiActionContribution('constellations.explore', 'Explorer les liens',
            self.from_book, target='book.detail', visible_when=lambda c: c.book is not None),))]

    def from_book(self, host):
        self.pending = host.action_context().book
        if self.view:
            self.view.seed(self.pending)
        host.navigate('constellations')

    def build(self, host):
        from gi.repository import Gtk, GLib
        self.view = View(self, host, Gtk, GLib)
        return self.view.widget

    def deactivate(self):
        if self.view:
            self.view.dispose()
        self.view = self.pending = None


class View:
    def __init__(self, plugin, host, Gtk, GLib):
        self.plugin, self.host, self.Gtk, self.GLib = plugin, host, Gtk, GLib
        self.nodes, self.local, self.cache = (), (), {}
        self.nav, self.center, self.path = Navigation(), None, ()
        self.generation, self.task, self.disposed = 0, None, False
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.status = Gtk.Label(label='Choisissez un profil pour explorer sa bibliothèque.', wrap=True, xalign=0)
        self.widget.append(self.status)
        self.identities = list(plugin.context.broker('identities').identities())
        self.identity = Gtk.DropDown.new_from_strings([str(r.get('label') or r.get('name') or r.get('identity_id')) for r in self.identities])
        self.identity.set_tooltip_text('Profil de bibliothèque')
        self.widget.append(self.identity)
        self.identity.connect('notify::selected', lambda *_: self.load())
        controls = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5)
        self.widget.append(controls)
        self.mode = Gtk.DropDown.new_from_strings(['Explorateur', 'Confort', 'Projet', 'Humeur'])
        self.mode.connect('notify::selected', lambda *_: self.render())
        controls.append(self.mode)
        self.signal = Gtk.Entry(placeholder_text='Sujet du projet / humeur attestée')
        self.signal.connect('changed', lambda *_: self.render())
        controls.append(self.signal)
        self.surprise = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.surprise.set_value(25); self.surprise.set_size_request(180, -1)
        self.surprise.set_tooltip_text('Proche (0) ↔ surprenant (100)')
        self.surprise.update_property([Gtk.AccessibleProperty.LABEL], ['Proche à surprenant'])
        self.surprise.connect('value-changed', lambda *_: self.render())
        controls.append(self.surprise)
        self.query = Gtk.Entry(placeholder_text='Requête catalogue — envoi explicite')
        controls.append(self.query)
        controls.append(self.button('Chercher en ligne', lambda: self.load(remote=self.query.get_text().strip())))
        controls.append(self.button('Actualiser la bibliothèque', lambda: self.load(refresh=True)))
        controls.append(self.button('Précédent', lambda: self.move(-1)))
        controls.append(self.button('Suivant', lambda: self.move(1)))
        controls.append(self.button('Ouvrir la fiche Bibliothèque', self.open_book))
        self.heading = Gtk.Label(wrap=True, xalign=0)
        self.widget.append(self.heading)
        self.variant = Gtk.DropDown.new_from_strings(['court', 'équilibré', 'surprenant'])
        controls.append(self.variant)
        controls.append(self.button('Choisir le départ', self.start_walk))
        controls.append(self.button('Rejoindre ce livre', self.finish_walk))
        self.start = None
        self.master = host.build_book_master_detail(title='Livre central', empty_title='Aucun voisin attesté')
        self.widget.append(self.master.widget)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=1)
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=200)
        scroll.set_child(self.flow)
        self.widget.append(scroll)
        self.choices = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3)
        chooser = Gtk.Expander(label='Choisir un autre livre dans les notices chargées')
        chooser.set_child(self.choices)
        self.widget.append(chooser)
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
            self.nodes, self.local, self.center, self.path = (), (), None, ()
            self.render()
            self.status.set_label('Aucun profil disponible. Créez un profil dans Cifereca.')
            return
        self.nodes, self.local, self.center, self.path = (), (), None, ()
        self.nav, self.start = Navigation(), None
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
        self.status.set_label(f'{len(self.nodes)} notices · exploration bornée aux 300 premières notices locales et 100 résultats distants')
        self.plugin.pending = None
        if seed_key:
            self.visit(next(n for n in self.nodes if n.key == seed_key))
        elif self.nodes:
            self.visit(self.nodes[0])
        else:
            self.render()
        return False

    def seed(self, book):
        if book is None:
            return
        self.start, self.path = None, ()
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

    def start_walk(self):
        self.cancel_calculation()
        self.start = self.center
        if self.start:
            self.status.set_label('Départ : ' + self.start.book.title + '. Choisissez maintenant un livre d’arrivée.')

    def finish_walk(self):
        if not self.start or not self.center:
            self.status.set_label('Choisissez un départ et une arrivée distincts.'); return
        variant = ('court', 'équilibré', 'surprenant')[self.variant.get_selected()]
        nodes, start, end = self.nodes, self.start, self.center
        self.status.set_label('Recherche de promenade…')
        def apply(path):
            self.path = path
            self.status.set_label('Promenade : ' + ' → '.join(n.book.title for n in path) if path else 'Aucun chemin de 4 à 8 livres suffisamment documenté.')
            self.render()
        self.run_calculation(lambda control: promenade(nodes, start, end, variant=variant, control=control), apply, 'Calcul de promenade')

    def replace(self, index):
        path, nodes = self.path, self.nodes
        def apply(replacement):
            self.path = replacement
            self.render()
        self.run_calculation(lambda control: replace_step(path, index, nodes), apply, 'Remplacement d’étape')

    def render(self):
        if not hasattr(self, 'flow'):
            return
        child = self.flow.get_first_child()
        while child:
            self.flow.remove(child); child = self.flow.get_first_child()
        child = self.choices.get_first_child()
        while child:
            self.choices.remove(child); child = self.choices.get_first_child()
        if not self.center:
            self.heading.set_label('Aucun livre central')
            self.master.empty_detail(); return
        node = self.center
        self.heading.set_label('Centre : ' + node.book.title)
        links = (DocumentLink('notice', 'Voir la notice en ligne', node.url),) if node.url else ()
        self.master.show_detail(UiBookDetailPresentation(node.book.title, ', '.join(node.book.authors),
            badges=node.availability, links=links, sections=(DetailSection('provenance', 'Provenance',
            (DetailField('sources', 'Sources', ', '.join(node.sources)),)),)))
        mode = ('Explorateur', 'Confort', 'Projet', 'Humeur')[self.mode.get_selected()]
        edges = neighbours(self.nodes, node, mode=mode, surprise=self.surprise.get_value()/100,
            theme=self.signal.get_text(), mood=self.signal.get_text(), profile=self.local)
        if not edges:
            self.flow.append(self.Gtk.Label(label='Aucun lien attesté pour ce mode. Choisissez un livre ci-dessous ou cherchez en ligne.', wrap=True))
        for edge in edges:
            text = edge.target.book.title + '\n' + ', '.join(edge.target.availability) + '\n' + '\n'.join(edge.reasons)
            b = self.button(text, lambda n=edge.target: self.visit(n))
            b.get_child().set_wrap(True)
            self.flow.append(b)
        # Catalogue selector remains available when metadata cannot produce edges.
        for other in self.nodes[:100]:
            if other.key != node.key and all(e.target.key != other.key for e in edges):
                b = self.button('Choisir : ' + other.book.title, lambda n=other: self.visit(n))
                b.get_child().set_wrap(True); self.choices.append(b)
        for i, step in enumerate(self.path):
            reason = 'Départ' if i == 0 else '; '.join(relation(self.path[i-1], step).reasons)
            self.flow.append(self.Gtk.Label(label=f'{i+1}. {step.book.title} · {", ".join(step.availability)}\n{reason}', wrap=True))
            if 0 < i < len(self.path)-1:
                self.flow.append(self.button('Remplacer étape ' + str(i+1), lambda index=i: self.replace(index)))

    def dispose(self):
        self.disposed = True; self.generation += 1
        if self.task:
            self.task.cancel()
        self.unsubscribe(); self.cache.clear()


PLUGIN = ConstellationsPlugin()
