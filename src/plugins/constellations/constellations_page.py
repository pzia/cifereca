"""Constellations page and contextual action, using public Plugin API only."""
from collections.abc import Mapping
from cifereca.plugin_api import (UiPageContribution, UiActionContribution,
    UiBookDetailPresentation, DetailSection, DetailField, DocumentLink)
from constellation_view_model import (node_from_row, merge_nodes, neighbours, relation,
    promenade, replace_step, Navigation)


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
        config = getattr(plugin.context, 'config', {}) or {}
        self._semantic_enabled = config.get('llm_semantic_hypotheses') is True if isinstance(config, Mapping) else False
        self._semantic_generation, self._semantic_task = 0, None
        self._semantic_candidate = None
        self._semantic_button, self._semantic_note = None, None
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.status = Gtk.Label(label='Choisissez un profil pour explorer sa bibliothèque.', wrap=True, xalign=0)
        self.widget.append(self.status)
        self.identities = list(plugin.context.broker('identities').identities())
        self.identity = Gtk.DropDown.new_from_strings([str(r.get('label') or r.get('name') or r.get('identity_id')) for r in self.identities])
        self.identity.set_tooltip_text('Profil de bibliothèque')
        self.widget.append(self.identity)
        self.identity.connect('notify::selected', lambda *_: self.load())
        # Explicitly grouped intents; catalog calls remain click-only.
        sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(sections)

        def group(title):
            sections.append(Gtk.Label(label=title, xalign=0))
            line = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5)
            sections.append(line)
            return line

        explore = group('Explorer')
        self.mode = Gtk.DropDown.new_from_strings(['Explorateur', 'Confort', 'Projet', 'Humeur'])
        self.mode.set_tooltip_text('Mode de proximité entre livres')
        self.mode.connect('notify::selected', lambda *_: self.render())
        explore.append(self.mode)
        self.signal = Gtk.Entry(placeholder_text='Sujet du projet / humeur attestée')
        self.signal.connect('changed', lambda *_: self.render())
        explore.append(self.signal)

        distance = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        distance.append(Gtk.Label(label='Proche'))
        self.surprise = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.surprise.set_value(25)
        self.surprise.set_size_request(160, -1)
        self.surprise.set_digits(0)
        self.surprise.set_draw_value(True)
        self.surprise.set_tooltip_text('Proche (0) à surprenant (100)')
        self.surprise.update_property([Gtk.AccessibleProperty.LABEL], ['Proximité de 0 à 100'])
        self.surprise.connect('value-changed', lambda *_: self.render())
        distance.append(self.surprise)
        distance.append(Gtk.Label(label='Surprenant'))
        explore.append(distance)
        explore.append(self.button('Précédent', lambda: self.move(-1)))
        explore.append(self.button('Suivant', lambda: self.move(1)))
        explore.append(self.button('Ouvrir la fiche Bibliothèque', self.open_book))
        if self._semantic_enabled:
            self._semantic_button = self.button('Piste sémantique (IA)', self.propose_semantic)
            self._semantic_button.set_sensitive(False)
            self._semantic_button.set_tooltip_text(
                'Clic explicite : compare seulement des sujets attestés entre deux notices sans lien confirmé. '
                'Le service IA configuré peut être distant ; aucune arête réelle n’est créée.'
            )
            explore.append(self._semantic_button)

        search = group('Chercher')
        self.query = Gtk.Entry(placeholder_text='Requête catalogue — envoi explicite')
        search.append(self.query)
        search.append(self.button('Chercher en ligne', lambda: self.load(remote=self.query.get_text().strip())))
        search.append(self.button('Actualiser la bibliothèque', lambda: self.load(refresh=True)))

        walk = group('Parcours')
        self.variant = Gtk.DropDown.new_from_strings(['court', 'équilibré', 'surprenant'])
        walk.append(self.variant)
        walk.append(self.button('Choisir le départ', self.start_walk))
        walk.append(self.button('Rejoindre ce livre', self.finish_walk))
        self.heading = Gtk.Label(wrap=True, xalign=0)
        self.widget.append(self.heading)
        self.start = None
        self.master = host.build_book_master_detail(title='Livre central', empty_title='Aucun livre sélectionné dans cette liste')
        self.widget.append(self.master.widget)
        self.neighbours_summary = Gtk.Label(label='Aucun voisin proposé', wrap=True, xalign=0)
        self.widget.append(self.neighbours_summary)
        if self._semantic_enabled:
            self._semantic_note = Gtk.Label(
                label='IA facultative : aucune hypothèse calculée ni relation ajoutée.',
                wrap=True, xalign=0,
            )
            self._semantic_note.set_selectable(True)
            self.widget.append(self._semantic_note)
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

    def _reset_semantic(self):
        self._semantic_generation += 1
        handle, self._semantic_task = self._semantic_task, None
        if handle is not None:
            handle.cancel()
        self._semantic_candidate = None
        if self._semantic_button is not None:
            self._semantic_button.set_sensitive(False)
            self._semantic_note.set_label(
                'IA facultative : aucune piste calculée. Le graphe déterministe reste inchangé.'
            )

    def propose_semantic(self):
        """Opt-in hypothesis only; never append an edge or modify ranking."""
        if not self._semantic_enabled or self._semantic_task is not None or self.disposed:
            return
        center, candidate = self.center, self._semantic_candidate
        identity = self.identity_id()
        if center is None or candidate is None or not identity:
            return
        context = self.plugin.context
        service = context.service('llm') if context is not None else None
        tasks = context.broker('tasks') if context is not None else None
        if service is None or tasks is None:
            self._semantic_note.set_label('Service IA non configuré. Le graphe reste consultable.')
            return
        try:
            from semantic_llm import propose_semantic_link
        except ImportError:
            self._semantic_note.set_label('Extension IA indisponible. Le graphe reste consultable.')
            return
        self._semantic_generation += 1
        generation = self._semantic_generation
        self._semantic_button.set_sensitive(False)
        self._semantic_note.set_label('Proposition volontaire en cours… Aucun lien ajouté au graphe.')
        try:
            handle = tasks.submit(
                lambda control: propose_semantic_link(
                    service, center, candidate, enabled=True, consent=True, control=control,
                ),
                label='Hypothèse sémantique Constellations', kind='constellations.llm',
            )
        except Exception:
            self._semantic_note.set_label('IA indisponible. Voisinage inchangé.')
            self._semantic_button.set_sensitive(True)
            return
        self._semantic_task = handle

        def completed(result_handle):
            if result_handle.control.cancelled():
                return
            try:
                hint = result_handle.result()
            except Exception:
                hint = None

            def apply():
                if (self.disposed or self.plugin.context is None
                        or generation != self._semantic_generation
                        or identity != self.identity_id()
                        or self.center is None or self.center.key != center.key):
                    return False
                self._semantic_task = None
                self._semantic_button.set_sensitive(True)
                if hint is None:
                    self._semantic_note.set_label(
                        'Aucune piste IA exploitable. Les relations attestées restent inchangées.'
                    )
                else:
                    self._semantic_note.set_label(
                        'Piste sémantique inférée — NON attestée, hors graphe : '
                        + center.book.title + ' → ' + candidate.book.title
                        + ' · ' + hint.text
                        + f' · {hint.provider}/{hint.model}'
                        + (' · distant' if hint.remote else ' · local')
                        + '. Ne confirme aucune relation ni droit.'
                    )
                return False

            self.GLib.idle_add(apply)

        handle.add_done_callback(completed)

    def render(self):
        if not hasattr(self, 'flow'):
            return
        if hasattr(self, '_semantic_generation'):
            self._reset_semantic()
        child = self.flow.get_first_child()
        while child:
            self.flow.remove(child); child = self.flow.get_first_child()
        child = self.choices.get_first_child()
        while child:
            self.choices.remove(child); child = self.choices.get_first_child()
        if not self.center:
            self.heading.set_label('Aucun livre central')
            self.neighbours_summary.set_label('Choisissez un livre pour afficher son voisinage')
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
        self.neighbours_summary.set_label(neighbour_count_label(edges))
        if self._semantic_button is not None:
            try:
                from semantic_llm import pick_unlinked_pair
                self._semantic_candidate = pick_unlinked_pair(self.nodes, node)
            except ImportError:
                self._semantic_candidate = None
            self._semantic_button.set_sensitive(self._semantic_candidate is not None)
            if self._semantic_candidate is not None:
                self._semantic_note.set_label('IA facultative : cliquer pour examiner une notice sans lien attesté, sans changer le graphe.')
        if not edges:
            self.flow.append(self.Gtk.Label(
                label='Aucun lien proposé pour ce mode. Choisissez un autre livre ou cherchez en ligne.',
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
        if hasattr(self, '_semantic_generation'):
            self._reset_semantic()
        self.disposed = True; self.generation += 1
        if self.task:
            self.task.cancel()
        self.unsubscribe(); self.cache.clear()


PLUGIN = ConstellationsPlugin()
