"""Constellations page and contextual action, using public Plugin API only."""
from collections.abc import Mapping
from cifereca.plugin_api import (UiPageContribution, UiActionContribution,
    UiBookDetailPresentation, DetailSection, DetailField, DocumentLink)
from constellation_view_model import (node_from_row, merge_nodes, neighbours,
    promenade, replace_step, Navigation)
from itertools import islice

# Read no more than N+1 books from the metadata broker, even if it yields
# thousands of entries or an unbounded iterator.
LOCAL_NODE_LIMIT = 300
PAGE_CACHE_PROFILES = 8


def constellation_detail_presentation(node) -> UiBookDetailPresentation:
    """Public book-detail projection with individual bibliographic values.

    The host continues to render its single DetailPanel. The graphed node
    contributes neither an acquisition action nor proof of entitlement.
    """
    authors = tuple(
        str(value).strip() for value in tuple(node.book.authors or ())
        if str(value).strip()
    )
    sources = tuple(
        str(value).strip() for value in tuple(node.sources or ())
        if str(value).strip()
    )
    sections = []
    if authors:
        sections.append(DetailSection(
            'bibliography', 'Bibliographie',
            tuple(
                DetailField(f'author-{index}', 'Auteur', value, kind='author')
                for index, value in enumerate(authors)
            ), order=10,
        ))
    if sources:
        sections.append(DetailSection(
            'provenance', 'Provenance',
            tuple(
                DetailField(f'source-{index}', 'Source', value, kind='copy')
                for index, value in enumerate(sources)
            ), order=20,
        ))
    links = (
        (DocumentLink('notice', 'Voir la notice en ligne', node.url),)
        if node.url else ()
    )
    return UiBookDetailPresentation(
        title=str(node.book.title or ''),
        subtitle=', '.join(authors),
        badges=tuple(node.availability or ()),
        links=links,
        sections=tuple(sections),
    )


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
        config = getattr(plugin.context, 'config', {}) or {}
        self._semantic_enabled = config.get('llm_semantic_hypotheses') is True if isinstance(config, Mapping) else False
        self._semantic_generation, self._semantic_task = 0, None
        self._semantic_candidate = None
        self._semantic_button, self._semantic_note = None, None
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
        if self._semantic_enabled:
            self._semantic_button = self.button('Piste sémantique (IA)', self.propose_semantic)
            self._semantic_button.set_sensitive(False)
            self._semantic_button.set_tooltip_text(
                'Clic explicite : compare seulement des sujets attestés entre deux notices sans lien confirmé. '
                'Le service IA configuré peut être distant ; aucune arête réelle n’est créée.'
            )
            self.controls.append(self._semantic_button)

        self.heading = Gtk.Label(wrap=True, xalign=0)
        self.widget.append(self.heading)
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
        if cached is not None:
            # Refresh page-cache LRU order without reading from the broker.
            self.cache[identity] = self.cache.pop(identity)
        # A contextual book from profile A must not be injected into B.
        pending_identity = str(getattr(self.plugin, 'pending_identity', '') or '')
        pending = self.plugin.pending if not pending_identity or pending_identity == identity else None
        context = self.plugin.context
        self.status.set_label('Chargement…')
        def operation(control):
            if control.cancelled():
                return None
            if cached is None:
                # Use N+1 to detect truncation *without* exhausting the
                # metadata iterator or retaining the rest of the catalogue.
                entries = context.broker('metadata').library_entries(identity_id=identity, scan=False)
                sample = tuple(islice(iter(entries), LOCAL_NODE_LIMIT + 1))
                truncated = len(sample) > LOCAL_NODE_LIMIT
                rows = sample[:LOCAL_NODE_LIMIT]
            else:
                rows, truncated = cached
            if control.cancelled():
                return None
            local = tuple(node_from_row(r, origin='bibliothèque') for r in rows)
            extra = ()
            if remote and not control.cancelled():
                extra = tuple(node_from_row(r) for r in context.broker('catalog').search(remote, identity_id=identity, limit=100)[:100])
            if control.cancelled():
                return None
            seed = node_from_row(pending, origin='sélection') if pending else None
            nodes = merge_nodes(local + extra + ((seed,) if seed else ()))
            return rows, local, nodes, seed.key if seed else None, truncated
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
        rows, self.local, self.nodes, seed_key, *extra = result
        truncated = bool(extra[0]) if extra else False
        self.cache.pop(identity, None)
        self.cache[identity] = (rows, truncated)
        while len(self.cache) > PAGE_CACHE_PROFILES:
            self.cache.pop(next(iter(self.cache)))
        coverage = (
            f'{LOCAL_NODE_LIMIT} premières notices locales seulement ; '
            'bibliothèque plus grande que le plafond'
            if truncated else f'{len(rows)} notices locales examinées'
        )
        self.status.set_label(
            f'{coverage} · {len(self.nodes)} nœud(s) pour ce profil · '
            'voisinage local sans collecte automatique'
        )
        if seed_key or not getattr(self.plugin, 'pending_identity', '') or (
            self.plugin.pending_identity == identity
        ):
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

    # Legacy engine adapter kept only for internal/backward compatibility.
    # Neither these operations nor the departure/arrival controls are exposed
    # anywhere in the new P2 book-first page (route planning is P3 #703).
    def finish_walk(self):
        if not getattr(self, 'start', None) or not self.center:
            self.status.set_label('Parcours non disponible dans cette vue.')
            return
        variant_picker = getattr(self, 'variant', None)
        selected = variant_picker.get_selected() if variant_picker is not None else 1
        variant = ('court', 'équilibré', 'surprenant')[selected]
        nodes, start, end = self.nodes, self.start, self.center
        self.status.set_label('Calcul du parcours historique…')
        def apply(path):
            self.path = path
            self.render()
        self.run_calculation(
            lambda control: promenade(nodes, start, end, variant=variant, control=control),
            apply, 'Calcul du parcours historique',
        )

    def replace(self, index):
        path, nodes = self.path, self.nodes
        def apply(replacement):
            self.path = replacement
            self.render()
        self.run_calculation(
            lambda control: replace_step(path, index, nodes),
            apply, 'Remplacement historique',
        )

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
        back = getattr(self, 'back_button', None)
        forward = getattr(self, 'forward_button', None)
        origin = getattr(self, 'origin_button', None)
        navigation = getattr(self, 'nav', None)
        if back is not None and navigation is not None:
            back.set_sensitive(navigation.position > 0)
        if forward is not None and navigation is not None:
            forward.set_sensitive(0 <= navigation.position < len(navigation.history) - 1)
        if origin is not None:
            origin.set_sensitive(getattr(self, 'origin_book', None) is not None)
        if not self.center:
            self.heading.set_label('Choisissez un livre dans Recherche ou Bibliothèque')
            self.neighbours_summary.set_label('Aucun point de départ. Utilisez « Explorer les livres proches » depuis une fiche.')
            self.master.empty_detail(); return
        node = self.center
        self.heading.set_label('Centre : ' + node.book.title)
        self.master.show_detail(constellation_detail_presentation(node))
        # A single stable ranking keeps the first exploration comprehensible.
        edges = neighbours(self.nodes, node, mode='Confort', surprise=.25, profile=self.local, limit=12)
        self.neighbours_summary.set_label(neighbour_count_label(edges))
        if getattr(self, '_semantic_button', None) is not None:
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
        if hasattr(self, '_semantic_generation'):
            self._reset_semantic()
        self.disposed = True; self.generation += 1
        if self.task:
            self.task.cancel()
        self.unsubscribe(); self.cache.clear()


PLUGIN = ConstellationsPlugin()
