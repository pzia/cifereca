"""External identity dashboard; uses only the public UiIdentityHost broker."""
from cifereca.plugin_api import UiPageContribution
import re

STATUS={'running':'En cours','success':'Réussie','partial':'Partielle','failed':'Échouée'}

def safe_issue(value):
    text=str(value)
    if any(word in text for word in ('ContentKeys','content_keys','fulfillmentToken')):
        return 'Contenu sensible omis'
    text=re.sub(r'https?://[^\s]+','<URL masquée>',text,flags=re.I)
    text=re.sub(r'(?i)Bearer\s+[^\s,;]+','Bearer <masqué>',text)
    text=re.sub(r'(?i)\b(?:password|token|cookie|secret|authorization|key)\s*[:=]\s*[^\s,;]+','<secret masqué>',text)
    text=re.sub(r'\b[A-Za-z0-9+/=_-]{40,}\b','<valeur masquée>',text)
    return text[:400]

def issues_text(sync):
    detail=sync.get('detail') or {};lines=[]
    for key in ('acquisition_errors','provider_errors'):
        for row in (detail.get(key) or [])[:20]:
            if isinstance(row,dict):
                title=safe_issue(row.get('title') or row.get('provider') or 'Opération')
                cause=safe_issue(row.get('error') or row.get('code') or 'Action requise')
                lines.append(title+' · '+cause)
    if detail.get('error'):lines.append(safe_issue(detail['error']))
    return '\n\n'.join(lines) or 'Ancienne synchronisation sans détail. Relancez-la ou consultez Récents.'

PUBLIC_PROVIDERS = {'gallica', 'gutenberg', 'bnf-catalogue', 'baen', 'ebooksgratuits', 'numilog-catalog'}
LOCAL_PROVIDERS = {'local'}
SOURCE_NAMES = {
    'local':'Local',
    'paris':'Bibliothèques de Paris',
    'kobo':'Kobo',
    'numilog':'Numilog',
    'numilog-catalog':'Numilog — Catalogue public',
    'gallica':'Gallica',
    'gutenberg':'Gutenberg',
    'bnf-catalogue':'BnF',
    'baen':'Baen',
    'ebooksgratuits':'Ebooks libres et gratuits',
}

def source_groups(row, public_providers=PUBLIC_PROVIDERS, local_providers=LOCAL_PROVIDERS):
    linked=set(row.get('providers') or ())
    # Only the three public fields of the dashboard projection are used.
    # Future broker metadata (URLs, tokens, raw failures) must never hitchhike
    # through an Accueil source card or be exposed by a diagnostic repr.
    observed={
        source['provider_id']: {
            'provider_id': source['provider_id'],
            'label': source.get('label') if type(source.get('label')) is str else '',
            'connected': source.get('connected') is True,
        }
        for source in (row.get('sources') or ())
        if isinstance(source, dict)
        and type(source.get('provider_id')) is str and source['provider_id']
    }
    stats=row.get('statistics') if isinstance(row.get('statistics'),dict) else {}
    counts=stats.get('sources') if isinstance(stats.get('sources'),dict) else {}
    candidates=set(linked)|set(observed)|set(counts)
    if int(stats.get('local_books') or 0)>0:
        candidates.add('local')

    def build(pid,kind):
        item=dict(observed.get(pid) or {})
        item.setdefault('provider_id',pid)
        item.setdefault('label',SOURCE_NAMES.get(pid,pid))
        count=counts.get(pid)
        if count is None and pid=='local' and stats.get('local_books') is not None:
            count=stats.get('local_books')
        if count is not None:
            item['count']=count
        item['kind']=kind
        if kind=='authenticated':
            item.setdefault('connected',False)
        return item

    local=[build(pid,'local') for pid in sorted(candidates & set(local_providers))]
    public=[
        build(pid, 'public')
        for pid in sorted(candidates & set(public_providers))
        # Numilog's commercial public source intentionally creates NO
        # Account. Only a live enabled-session descriptor in the Core's
        # dashboard snapshot may prove it is currently available.
        # Historical placement origins cannot re-enable a disabled plugin.
        if pid != 'numilog-catalog' or pid in observed
    ]
    authenticated=[build(pid,'authenticated') for pid in sorted(linked-set(public_providers)-set(local_providers))]
    return {'local':local,'public':public,'authenticated':authenticated}

def linked_sources(row, public_providers=PUBLIC_PROVIDERS):
    return source_groups(row,public_providers)['authenticated']

def readers_text(row):
    readers=row.get('associated_readers')
    if readers is None:
        return 'Liseuses : information non disponible'
    if not readers:
        return 'Liseuses : aucune associée'
    return 'Liseuses : '+', '.join(str(r.get('label') or 'Liseuse') for r in readers if isinstance(r,dict))

# Current, explicitly non-loan providers only. Unknown providers fail open
# to the existing "loans not refreshed" warning: this is presentation logic,
# not a provider capability registry or a source entitlement inference.
_NON_LOAN_PROVIDERS = frozenset({
    'kobo', 'numilog', 'numilog-catalog', 'gallica', 'gutenberg',
    'bnf-catalogue', 'baen', 'ebooksgratuits', 'local',
})


def _loan_scope_not_configured(row):
    """True only for an explicitly non-loan provider-only identity.

    Never treat a missing provider inventory, unseen plugin, observed loan,
    or an explicit Core loans capability as a known absence of lending.
    Source origins (statistics.sources) are NOT connected providers.
    """
    if not isinstance(row, dict):
        return False
    providers = row.get('providers')
    if (type(providers) not in (list, tuple, set, frozenset)
            or len(providers) > 128
            or any(type(pid) is not str for pid in providers)):
        return False
    if any(pid not in _NON_LOAN_PROVIDERS for pid in providers):
        return False
    sources = row.get('sources')
    if type(sources) in (list, tuple):
        if (len(sources) > 128
                or any(
                    not isinstance(item, dict)
                    or type(item.get('provider_id')) is not str
                    or item['provider_id'] not in _NON_LOAN_PROVIDERS
                    for item in sources
                )):
            return False
    # Public singleton catalogues have no Account by design. A valid,
    # nonempty, Core-provided live source list is enough when the explicit
    # account list is empty; neither historical file origins nor a missing
    # inventory is treated as a current connection.
    if not providers:
        if (type(sources) not in (list, tuple)
                or not sources
                or any(item['provider_id'] not in PUBLIC_PROVIDERS
                       for item in sources)):
            return False
    capabilities = row.get('capabilities')
    if isinstance(capabilities, (list, tuple)):
        # This Core-authoritative projection contains only public capability
        # names and labels; never read or copy provider-specific raw data.
        if any(isinstance(cap, dict) and cap.get('id') == 'loans'
               for cap in capabilities):
            return False
    stats = row.get('statistics')
    if not isinstance(stats, dict):
        return False
    # A loan observed before a provider was disconnected is not erased by a
    # newly non-lending source list, including the zero-loan observation case.
    observed = stats.get('loan_sources_observed')
    known = stats.get('known_loans')
    if type(observed) is int and observed > 0:
        return False
    if type(known) is int and known > 0:
        return False
    return True


def statistics_lines(row):
    stats=row.get('statistics')
    if not isinstance(stats,dict):return ['Statistiques : Core029 requis']
    loans=stats.get('known_loans')
    if _loan_scope_not_configured(row):
        loan_text = "aucune source de prêt configurée"
    else:
        loan_text = f"{loans if loans is not None else '—'} prêts de bibliothèque connus"
    lines=[f"{stats.get('local_books',0)} livres locaux · {loan_text}"]
    names={'paris':'Paris','kobo':'Kobo','gallica':'Gallica','gutenberg':'Gutenberg','bnf-catalogue':'BnF','numilog':'Numilog','local':'Local (origine non rapprochée)'}
    sources=stats.get('sources') or {}
    if sources:lines.append('Livres par catalogue : '+ ' · '.join(f"{names.get(key,key)} : {count}" for key,count in sorted(sources.items())))
    lines.append(f"{stats.get('local_placements',0)} emplacements · {stats.get('remote_owned_rights',0)} droits d’achat distants")
    if loans is None and not _loan_scope_not_configured(row):
        lines.append('Prêts de bibliothèque non actualisés')
    lines.append('Inventaire local et droits d’achat distincts des prêts de bibliothèque. Les sources peuvent se chevaucher.')
    return lines

def overview_counts_lines(stats, *, row=None):
    """Keep physical files, store-owned rights and library loans distinct.

    Missing observations are unknown, never zero. These counts may overlap
    by book identity; they must not be summed into a fictitious total.
    """
    def number(key):
        value = stats.get(key)
        return value if type(value) is int and value >= 0 else '—'

    loan_text = (
        "aucune source de prêt configurée"
        if row is not None and _loan_scope_not_configured(row)
        else f"prêts de bibliothèque connus : {number('known_loans')}"
    )
    return (
        f"Livres locaux : {number('local_books')} · {loan_text}",
        f"Droits d’achat distants : {number('remote_owned_rights')} "
        "(ne prouvent pas la présence d’un fichier local)",
    )


def attention_items(row, *, now=None):
    """Return prioritized, deduplicated actions from already-known local state."""
    from time import time
    stats=row.get('statistics') if isinstance(row.get('statistics'),dict) else {}
    sync=row.get('sync') if isinstance(row.get('sync'),dict) else {}
    items=[]
    def add(key,kind,label,*,count=None,priority=50,action='open',action_label='Voir',context='',provider_id=None):
        if count is not None and int(count or 0)<=0:
            return
        items.append({
            'key':key,'kind':kind,'label':label,'count':count,'priority':priority,
            'action':action,'action_label':action_label,'context':context,
            'provider_id':provider_id,
        })

    due=int(stats.get('due_soon_loans') or 0)
    if due:
        add('loans-due','warning','Prêts proches de l’échéance',count=due,priority=80,
            action='open',action_label='Voir',context='Bibliothèque · échéances')
    available=int(stats.get('available_bookings') or stats.get('available_reservations') or 0)
    if available:
        add('bookings-available','success','Réservations disponibles',count=available,priority=90,
            action='open',action_label='Voir',context='Bibliothèque · réservations')

    attention=int(sync.get('attention_required') or 0)
    errors=int(sync.get('errors') or 0)
    sync_problem=bool(attention or errors)
    if sync_problem:
        label='Synchronisation à reprendre' if errors else 'Synchronisation à actualiser'
        action_label='Réessayer' if errors else 'Actualiser'
        detail=[]
        if errors:detail.append(f"{errors} erreur(s)")
        if attention:detail.append(f"{attention} action(s) requise(s)")
        add('sync','error' if errors else 'warning',label,count=max(errors,attention),priority=100 if errors else 70,
            action='sync',action_label=action_label,context='Synchronisation · '+', '.join(detail))

    loans_stale=False
    if stats and stats.get('known_loans') is None:
        loans_stale=True
    observed=stats.get('loans_observed_at')
    if stats.get('known_loans') and observed:
        try:
            loans_stale=max(0,float((time() if now is None else now))-float(observed))>24*3600
        except (TypeError,ValueError):
            pass
    # A failed/partial synchronization already owns the refresh action; avoid
    # presenting the same underlying freshness problem twice.
    if loans_stale and not sync_problem and not _loan_scope_not_configured(row):
        add('loans-stale','warning','Inventaire des prêts à actualiser',priority=60,
            action='sync',action_label='Actualiser',context='Prêts · données non actualisées')

    for source in linked_sources(row):
        if source.get('connected'):
            continue
        provider_id=str(source.get('provider_id') or '')
        label=str(source.get('label') or provider_id or 'Source')
        add('source:'+provider_id,'warning',label+' · connexion requise',priority=85,
            action='settings',action_label='Connecter',context='Source authentifiée · '+label,
            provider_id=provider_id)

    providers=set(row.get('providers') or ())
    if 'paris' in providers and not row.get('adept_profile_id'):
        add('ade-profile','warning','Profil ADE à associer',priority=55,
            action='open',action_label='Voir le détail',
            context='Bibliothèques de Paris · prêts protégés')
    return sorted(items,key=lambda item:(-int(item.get('priority') or 0),str(item.get('label') or '')))


def compact_sync_text(sync):
    """No zero is inferred from an absent count; status and errors stay visible."""
    sync=sync if isinstance(sync,dict) else {}
    def count(key):
        value=sync.get(key)
        return str(value) if type(value) is int and value>=0 else '—'
    return ('État : '+STATUS.get(sync.get('status'),'Jamais synchronisée')
            +' · acquisitions : '+count('new_acquisitions')
            +' · actions requises : '+count('attention_required')
            +' · erreurs : '+count('errors'))


def compact_source_groups(row, public_providers=PUBLIC_PROVIDERS):
    """Display each origin once, without promoting an unknown origin to login."""
    groups=source_groups(row,public_providers)
    stats=row.get('statistics') if isinstance(row.get('statistics'),dict) else {}
    counts=stats.get('sources') if isinstance(stats.get('sources'),dict) else {}
    seen={str(src['provider_id']) for rows in groups.values() for src in rows}
    other=[{'provider_id':key, 'label':SOURCE_NAMES.get(key,key), 'count':value,
            'kind':'unknown'} for key,value in sorted(counts.items())
           if isinstance(key,str) and key not in seen]
    return {'local':groups['local'],'public':groups['public'],
            'authenticated':groups['authenticated'],'unknown':other}


def compact_source_text(item, *, total_local_books=None):
    """Counts describe indexed book origins, not remote search availability."""
    label=str(item.get('label') or item.get('provider_id') or 'Source')
    kind=item.get('kind')
    count=item.get('count')
    has_count=type(count) is int and count>=0
    if kind=='local':
        # Local provenance == total local books in the common case; do not
        # repeat the exact same number underneath the main summary.
        amount=('origine locale (déjà comptée)' if has_count and
                type(total_local_books) is int and count==total_local_books
                else f"{count} livre(s) d’origine" if has_count else 'origine locale')
    elif kind=='public':
        amount=f"{count} livre(s) d’origine" if has_count else 'catalogue public'
    elif kind=='authenticated':
        status='connectée' if item.get('connected') is True else 'non connectée'
        amount=status+(f" · {count} livre(s) d’origine" if has_count else '')
    else:
        amount=f"{count} livre(s) d’origine" if has_count else 'origine non classée'
    return label+' · '+amount


class OverviewController:
    def __init__(self,host,render,identity_broker=None):
        self.host=host;self.render=render;self.identity_broker=identity_broker;self.closed=False
        self.unsubscribe=host.subscribe_identity_overview(self.refresh)
        try:self.refresh()
        except Exception:
            self.close();raise
    def refresh(self):
        if self.closed:
            return
        legacy_rows = list(self.host.identity_overview())
        if self.identity_broker is None:
            self.render(legacy_rows)
            return
        try:
            public_rows = list(self.identity_broker.identities())
        except Exception:
            public_rows = []
        if not public_rows:
            self.render(legacy_rows)
            return
        by_id = {str(row.get('identity_id') or row.get('id') or ''): dict(row) for row in legacy_rows if isinstance(row,dict)}
        merged=[]
        for public in public_rows:
            if not isinstance(public,dict):
                continue
            iid=str(public.get('identity_id') or public.get('id') or '')
            row=dict(public)
            if iid and iid in by_id:
                # Core037 broker identity fields are authoritative; UI host augments statistics/sync only.
                augmented=dict(by_id[iid]);augmented.update(row);row=augmented
            row.setdefault('identity_id',iid)
            merged.append(row)
        self.render(merged or legacy_rows)
    def open(self,identity_id):
        if not self.closed:self.host.open_identity_library(identity_id)
    def filter(self,identity_id,route,provider):
        support=getattr(self.host,'supports_identity_view_filters',None)
        broker=getattr(self.host,'open_identity_view',None)
        if not self.closed and callable(support) and support() is True and callable(broker):
            broker(identity_id,route=route,providers=(provider,));return True
        return False
    def synchronize(self,deep=False,control=None):
        if not self.closed:self.host.synchronize(deep=deep,control=control)
    def settings(self,provider_id):
        opener=getattr(self.host,'open_plugin_settings',None)
        if self.closed or not callable(opener) or not provider_id:
            return False
        opener('provider.'+str(provider_id));return True
    def close(self):
        if not self.closed:
            self.closed=True;self.unsubscribe();self.unsubscribe=lambda:None;self.render=None;self.host=None

def build_page(host,controllers,public_providers=PUBLIC_PROVIDERS,identity_broker=None):
    from gi.repository import Gtk, Pango
    required=('identity_overview','open_identity_library','synchronize','subscribe_identity_overview')
    if not all(callable(getattr(host,name,None)) for name in required):
        raise RuntimeError('Accueil nécessite les brokers publics du Core 0.9.15 ou ultérieur')
    scroll=Gtk.ScrolledWindow();scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
    outer=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
    for margin in ('top','bottom','start','end'):getattr(outer,'set_margin_'+margin)(16)
    header=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
    title=Gtk.Label(label='Bibliothèque familiale',xalign=0);title.set_hexpand(True);title.set_wrap(True);title.add_css_class('page-title');header.append(title)
    quick=Gtk.Button.new_from_icon_name('view-refresh-symbolic');quick.set_tooltip_text('Synchronisation rapide')
    deep=Gtk.Button.new_from_icon_name('system-search-symbolic');deep.set_tooltip_text('Vérifier toute la bibliothèque')
    header.append(quick);header.append(deep);outer.append(header)
    # Two compact identity panels per row, with comparison data above the fold.
    grid=Gtk.FlowBox();grid.set_selection_mode(Gtk.SelectionMode.NONE);grid.set_homogeneous(True)
    grid.set_min_children_per_line(2);grid.set_max_children_per_line(2);grid.set_column_spacing(12);grid.set_row_spacing(12);outer.append(grid)
    empty=Gtk.Label(label='Aucune identité configurée. Ouvrez Paramètres pour en créer une.',xalign=0);empty.set_wrap(True);outer.append(empty)
    scroll.set_child(outer)
    def label(parent,text,style=None):
        item=Gtk.Label(label=str(text),xalign=0);item.set_wrap(True)
        item.set_wrap_mode(Pango.WrapMode.WORD_CHAR);item.set_width_chars(1);item.set_max_width_chars(44);item.set_hexpand(True)
        if style:item.add_css_class(style)
        parent.append(item);return item
    def plain_section(parent,title_text):
        head=Gtk.Label(label=title_text,xalign=0)
        head.add_css_class('heading')
        parent.append(head)
        area=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
        parent.append(area)
        return area

    def compact_row(area,description,*,action=None,action_title='Voir',settings=None,tooltip=None):
        line=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
        line.set_hexpand(True)
        caption=Gtk.Label(label=str(description),xalign=0)
        caption.set_wrap(True)
        caption.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        caption.set_hexpand(True)
        caption.set_max_width_chars(68)
        if tooltip:caption.set_tooltip_text(tooltip)
        line.append(caption)
        if action is not None:
            button=Gtk.Button(label=action_title)
            button.add_css_class('flat')
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(tooltip or action_title)
            button.connect('clicked',action)
            line.append(button)
        if settings is not None:
            button=Gtk.Button.new_from_icon_name('emblem-system-symbolic')
            button.add_css_class('flat')
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text('Configurer cette source')
            button.connect('clicked',settings)
            line.append(button)
        area.append(line)
        return line

    def attention_entry(area,item,identity_id):
        # Attention stays primary and actionable, but is no longer a card
        # nested inside another card.
        row_box=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
        row_box.set_hexpand(True)
        row_box.add_css_class('error' if item.get('kind')=='error' else 'warning')
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=1)
        content.set_hexpand(True)
        context=str(item.get('context') or '').strip()
        if item.get('count') is not None:
            context=(context+' · ' if context else '')+str(item['count'])+' élément(s)'
        label(content,str(item.get('label') or 'Action requise'),'heading')
        if context:
            context_label=label(content,context,'caption')
            context_label.add_css_class('dim-label')
        row_box.append(content)
        action=str(item.get('action') or 'open')
        if action=='sync':
            callback=lambda _b:controller.synchronize(False)
        elif action=='settings':
            callback=lambda _b,pid=item.get('provider_id'):controller.settings(pid)
        else:
            callback=lambda _b,iid=identity_id:controller.open(iid)
        button=Gtk.Button(label=str(item.get('action_label') or 'Voir'))
        button.add_css_class('suggested-action')
        button.set_valign(Gtk.Align.CENTER)
        button.connect('clicked',callback)
        row_box.append(button)
        area.append(row_box)
        return row_box

    def render(rows):
        child=grid.get_first_child()
        while child is not None:
            next_child=child.get_next_sibling()
            grid.remove(child)
            child=next_child
        empty.set_visible(not rows)
        filter_supported=getattr(host,'supports_identity_view_filters',None)
        has_filters=bool(callable(filter_supported) and filter_supported() is True
                         and callable(getattr(host,'open_identity_view',None)))
        settings_open=getattr(host,'open_plugin_settings',None)

        for row in rows:
            iid=row.get('identity_id') or ''
            stats=row.get('statistics') if isinstance(row.get('statistics'),dict) else None
            sync=row.get('sync') if isinstance(row.get('sync'),dict) else {}
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            box.set_hexpand(True)
            box.add_css_class('card')
            for margin in ('top','bottom','start','end'):
                getattr(box,'set_margin_'+margin)(8)

            head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
            name=Gtk.Button(label=str(row.get('label') or 'Utilisateur'))
            name.set_halign(Gtk.Align.START)
            name.set_hexpand(True)
            name.add_css_class('flat')
            name.add_css_class('title-3')
            name.get_child().set_wrap(True)
            name.get_child().set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.get_child().set_max_width_chars(28)
            name.set_tooltip_text('Ouvrir la bibliothèque de ce profil')
            name.connect('clicked',lambda _button,identity=iid:controller.open(identity))
            head.append(name)
            state=Gtk.Label(label='ACTIVE' if row.get('selected') else 'AUTRE PROFIL')
            state.add_css_class('pill')
            state.add_css_class('success' if row.get('selected') else 'dim-label')
            state.set_valign(Gtk.Align.CENTER)
            head.append(state)
            box.append(head)

            attention=attention_items(row)
            if attention:
                attention_area=plain_section(box,'À traiter')
                # Never hide a real alert in a collapsed expander.
                for item in attention[:2]:
                    attention_entry(attention_area,item,iid)
                if len(attention)>2:
                    more=Gtk.Expander(label=f"{len(attention)-2} autre(s) action(s) à traiter")
                    inside=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
                    for item in attention[2:]:
                        attention_entry(inside,item,iid)
                    more.set_child(inside)
                    attention_area.append(more)

            summary=plain_section(box,'En bref')
            if stats is None:
                compact_row(summary,'Statistiques indisponibles · actualisation nécessaire')
            else:
                local_and_loans, owned_rights = overview_counts_lines(stats, row=row)
                compact_row(
                    summary,
                    local_and_loans,
                    action=lambda _b,identity=iid:controller.open(identity),
                    action_title='Bibliothèque',
                    tooltip='Ouvrir les fichiers locaux et prêts de cette identité. Les prêts peuvent être partiels.',
                )
                compact_row(summary, owned_rights)
                if stats.get('known_loans') is None and not _loan_scope_not_configured(row):
                    compact_row(summary,'Prêts de bibliothèque non actualisés · vérifier les alertes et la synchronisation')

            groups=compact_source_groups(row,public_providers)
            if any(groups.values()):
                sources_area=plain_section(box,'Sources et origines')
                for kind,title_text in (
                    ('local','Local'),
                    ('public','Catalogues publics'),
                    ('authenticated','Sources authentifiées'),
                    ('unknown','Autres origines'),
                ):
                    members=groups[kind]
                    if not members:
                        continue
                    subtitle=Gtk.Label(label=title_text,xalign=0)
                    subtitle.add_css_class('caption')
                    subtitle.add_css_class('dim-label')
                    sources_area.append(subtitle)
                    for source in members:
                        pid=str(source['provider_id'])
                        connected=source.get('connected') is True
                        route='library' if kind in ('local','unknown') else 'search'
                        if kind=='authenticated' and not connected:
                            action=None
                        elif has_filters:
                            action=lambda _b,identity=iid,provider=pid,dest=route:controller.filter(identity,dest,provider)
                        else:
                            action=None
                        settings=None
                        if kind=='authenticated' and connected and callable(settings_open):
                            settings=lambda _b,provider=pid:settings_open('provider.'+provider)
                        count=stats.get('local_books') if stats is not None else None
                        compact_row(
                            sources_area,
                            compact_source_text(source,total_local_books=count),
                            action=action,
                            action_title='Filtrer' if kind in ('local','unknown') else 'Chercher',
                            settings=settings,
                            tooltip='Origine du cache local ; ne préjuge pas de la disponibilité distante.'
                                    if kind in ('local','public','unknown')
                                    else 'Source authentifiée ; la connexion est indépendante des livres en cache.',
                        )

            sync_area=plain_section(box,'Synchronisation')
            compact_row(sync_area,compact_sync_text(sync))
            if sync.get('errors') or sync.get('attention_required'):
                # The alert is already visible in À traiter above. Detailed
                # diagnostic remains available on demand without leaking URL.
                details=Gtk.Expander(label='Détails des erreurs et actions')
                diagnostic=Gtk.Label(label=issues_text(sync),xalign=0)
                diagnostic.set_wrap(True)
                diagnostic.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                diagnostic.set_max_width_chars(62)
                details.set_child(diagnostic)
                sync_area.append(details)

            secondary=Gtk.Expander(label='Profil, appareils et inventaire')
            extra=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            if stats is not None:
                compact_row(extra,
                    f"Emplacements : {stats.get('local_placements') if stats.get('local_placements') is not None else '—'}"
                    +f" · droits d’achat distants : {stats.get('remote_owned_rights') if stats.get('remote_owned_rights') is not None else '—'}")
            ade='lié' if row.get('adept_profile_id') else 'non lié'
            compact_row(extra,'Compte ADE : '+ade+' · '+readers_text(row))
            if row.get('deep_last_at') is not None:
                from datetime import datetime
                try:
                    stamp=datetime.fromtimestamp(int(row['deep_last_at'])).astimezone().strftime('%d/%m/%Y %H:%M')
                except (TypeError,ValueError,OverflowError,OSError):
                    stamp='Date inconnue'
                compact_row(extra,'Dernier inventaire complet : '+stamp)
            compact_row(extra,'Les origines peuvent se chevaucher ; les comptes de prêts peuvent être partiels.')
            secondary.set_child(extra)
            box.append(secondary)
            grid.insert(box,-1)

    controller=OverviewController(host,render,identity_broker=identity_broker);controllers.append(controller)
    quick.connect('clicked',lambda button:controller.synchronize(False,button))
    deep.connect('clicked',lambda button:controller.synchronize(True,button))
    rooted=[False]
    def root_changed(widget,param):
        if widget.get_root() is not None:rooted[0]=True
        elif rooted[0]:controller.close()
    scroll.connect('notify::root',root_changed)
    return scroll

class HomePlugin:
    def activate(self,context):
        self.controllers=[];self.context=context
        broker=getattr(context,'broker',None) if context is not None else None
        self.identity_broker=broker('identities') if callable(broker) else None
        reporter=getattr(context,'report_diagnostic',None) if context is not None else None
        if self.identity_broker is None and callable(reporter):
            reporter('Accueil: broker identities indisponible','degraded',detail={'broker':'identities'},level='normal')
        return self
    def pages(self):
        return [UiPageContribution('dashboard','Accueil','view-dashboard-symbolic',0,self.build,'usage')]
    def build(self,host):
        public=set(PUBLIC_PROVIDERS)
        # Resolve optional provider services by their public Core041 contract, never
        # by implicit registry order or a private implementation type.
        if self.context is not None:
            for resolved in self.context.services(
                'provider.library', contract='cifereca.provider', version='>=1,<2'
            ):
                descriptor=getattr(resolved.service,'descriptor',None)
                if descriptor and getattr(descriptor,'instantiation',None)=='singleton_public':
                    public.add(descriptor.id)
        return build_page(host,self.controllers,public,identity_broker=self.identity_broker)
    def deactivate(self):
        for controller in getattr(self,'controllers',[]):controller.close()
        self.controllers=[]
        self.identity_broker=None
        self.context=None

PLUGIN=HomePlugin()
