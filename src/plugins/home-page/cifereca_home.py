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

PUBLIC_PROVIDERS = {'gallica', 'gutenberg', 'bnf-catalogue', 'baen', 'ebooksgratuits'}
LOCAL_PROVIDERS = {'local'}
SOURCE_NAMES = {
    'local':'Local',
    'paris':'Bibliothèques de Paris',
    'kobo':'Kobo',
    'gallica':'Gallica',
    'gutenberg':'Gutenberg',
    'bnf-catalogue':'BnF',
    'baen':'Baen',
    'ebooksgratuits':'Ebooks libres et gratuits',
}

def source_groups(row, public_providers=PUBLIC_PROVIDERS, local_providers=LOCAL_PROVIDERS):
    linked=set(row.get('providers') or ())
    observed={s.get('provider_id'):dict(s) for s in row.get('sources',[]) if isinstance(s,dict) and s.get('provider_id')}
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
    public=[build(pid,'public') for pid in sorted(candidates & set(public_providers))]
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

def statistics_lines(row):
    stats=row.get('statistics')
    if not isinstance(stats,dict):return ['Statistiques : Core029 requis']
    loans=stats.get('known_loans')
    lines=[f"{stats.get('local_books',0)} livres locaux · {loans if loans is not None else '—'} emprunts connus"]
    names={'paris':'Paris','kobo':'Kobo','gallica':'Gallica','gutenberg':'Gutenberg','bnf-catalogue':'BnF','local':'Local (origine non rapprochée)'}
    sources=stats.get('sources') or {}
    if sources:lines.append('Livres par catalogue : '+ ' · '.join(f"{names.get(key,key)} : {count}" for key,count in sorted(sources.items())))
    lines.append(f"{stats.get('local_placements',0)} emplacements · {stats.get('remote_owned_rights',0)} droits d’achat distants")
    if loans is None:lines.append('Emprunts non actualisés')
    lines.append('Dernier inventaire ; comptes de prêts éventuellement partiels. Les sources peuvent se chevaucher.')
    return lines

def attention_items(row, *, now=None):
    """Return concise dashboard actions from already-known local state."""
    from time import time
    stats=row.get('statistics') if isinstance(row.get('statistics'),dict) else {}
    sync=row.get('sync') if isinstance(row.get('sync'),dict) else {}
    items=[]
    def add(kind,label,count=None):
        if count is None or int(count or 0)>0:
            items.append({'kind':kind,'label':label,'count':count})
    due=int(stats.get('due_soon_loans') or 0)
    if due:add('warning','Prêts proches de l’échéance',due)
    available=int(stats.get('available_bookings') or stats.get('available_reservations') or 0)
    if available:add('success','Réservations disponibles',available)
    attention=int(sync.get('attention_required') or 0)
    if attention:add('warning','Actions de synchronisation requises',attention)
    errors=int(sync.get('errors') or 0)
    if errors:add('error','Erreurs de synchronisation',errors)
    if stats and stats.get('known_loans') is None:
        add('warning','Inventaire des prêts à actualiser')
    observed=stats.get('loans_observed_at')
    if stats.get('known_loans') and observed:
        try:
            age=max(0,float((time() if now is None else now))-float(observed))
            if age>24*3600:add('warning','Inventaire des prêts ancien')
        except (TypeError,ValueError):
            pass
    disconnected=[str(source.get('label') or source.get('provider_id') or 'Source')
                  for source in linked_sources(row) if not source.get('connected')]
    if disconnected:
        add('warning','Sources à connecter : '+', '.join(disconnected[:3]))
    providers=set(row.get('providers') or ())
    if 'paris' in providers and not row.get('adept_profile_id'):
        add('warning','Profil ADE à associer pour les prêts protégés')
    return items


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
    # Two identity cards per row; each card keeps its own three-column metric
    # sections. The outer grid owns card placement, the inner grids own metrics.
    grid=Gtk.FlowBox();grid.set_selection_mode(Gtk.SelectionMode.NONE);grid.set_homogeneous(True)
    grid.set_min_children_per_line(2);grid.set_max_children_per_line(2);grid.set_column_spacing(12);grid.set_row_spacing(12);outer.append(grid)
    empty=Gtk.Label(label='Aucune identité configurée. Ouvrez Paramètres pour en créer une.',xalign=0);empty.set_wrap(True);outer.append(empty)
    scroll.set_child(outer)
    def label(parent,text,style=None):
        item=Gtk.Label(label=str(text),xalign=0);item.set_wrap(True)
        item.set_wrap_mode(Pango.WrapMode.WORD_CHAR);item.set_width_chars(1);item.set_max_width_chars(44);item.set_hexpand(True)
        if style:item.add_css_class(style)
        parent.append(item);return item
    def section(parent,title_text,columns=3,icon=None):
        section_head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
        if icon is not None:
            section_icon=Gtk.Image.new_from_icon_name(icon);section_icon.add_css_class('dim-label');section_head.append(section_icon)
        label(section_head,title_text,'heading');parent.append(section_head)
        area=Gtk.Grid();area.set_column_homogeneous(True);area.set_column_spacing(6);area.set_row_spacing(6)
        area._cifereca_columns=columns;area._cifereca_next=0
        parent.append(area);return area
    def tile(area,caption,value,style='accent',tooltip=None,callback=None,settings_callback=None,span=1):
        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=0);content.set_hexpand(True);content.set_vexpand(True);content.add_css_class('card')
        inner=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=1);inner.set_hexpand(True);inner.set_vexpand(True);inner.set_valign(Gtk.Align.CENTER)
        for margin in ('top','bottom','start','end'):getattr(inner,'set_margin_'+margin)(6)
        content.append(inner)
        value_label=label(inner,value,style);value_label.add_css_class('heading')
        caption_label=label(inner,caption,'caption')
        for centered in (value_label,caption_label):
            centered.set_xalign(.5);centered.set_justify(Gtk.Justification.CENTER)
        if tooltip:value_label.set_tooltip_text(tooltip)
        main=content
        if callback is not None:
            action=Gtk.Button();action.set_child(content);action.add_css_class('flat');action.set_hexpand(True);action.set_vexpand(True)
            if tooltip:action.set_tooltip_text(tooltip)
            action.connect('clicked',callback);main=action
        if settings_callback is not None:
            wrapper=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=2);wrapper.set_hexpand(True)
            wrapper.append(main)
            settings=Gtk.Button();settings.set_child(Gtk.Image.new_from_icon_name('emblem-system-symbolic'));settings.add_css_class('flat')
            settings.set_tooltip_text('Configurer ce fournisseur');settings.connect('clicked',settings_callback);wrapper.append(settings)
            main=wrapper
        index=area._cifereca_next;columns=area._cifereca_columns;span=max(1,min(int(span),columns))
        column=index % columns
        if column+span>columns:
            index+=columns-column;column=0
        area.attach(main,column,index // columns,span,1);area._cifereca_next=index+span
        return main
    def render(rows):
        child=grid.get_first_child()
        while child is not None:
            next_child=child.get_next_sibling();grid.remove(child);child=next_child
        empty.set_visible(not rows)
        for row in rows:
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=7);box.set_hexpand(True);box.add_css_class('card')
            for margin in ('top','bottom','start','end'):getattr(box,'set_margin_'+margin)(10)
            head=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=10)
            avatar=Gtk.Image.new_from_icon_name('avatar-default-symbolic');avatar.set_pixel_size(28);avatar.add_css_class('accent');head.append(avatar)
            identity=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=0);identity.set_hexpand(True)
            name=Gtk.Button(label=str(row.get('label') or 'Utilisateur'));name.set_halign(Gtk.Align.START);name.add_css_class('flat');name.add_css_class('title-3')
            name.get_child().set_wrap(True);name.get_child().set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.get_child().set_max_width_chars(28);name.set_tooltip_text('Sélectionner cette identité et ouvrir sa bibliothèque')
            name.connect('clicked',lambda button,iid=row['identity_id']:controller.open(iid));identity.append(name)
            subtitle=Gtk.Label(label='Bibliothèque personnelle',xalign=0);subtitle.add_css_class('caption');subtitle.add_css_class('dim-label');identity.append(subtitle)
            head.append(identity)
            state=Gtk.Label(label='ACTIVE' if row.get('selected') else 'PROFIL');state.add_css_class('pill')
            state.add_css_class('success' if row.get('selected') else 'dim-label');state.set_valign(Gtk.Align.CENTER);head.append(state)
            box.append(head);box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

            stats=row.get('statistics')
            if isinstance(stats,dict):
                globals_grid=section(box,'En bref',3,'view-grid-symbolic')
                loans=stats.get('known_loans')
                tile(globals_grid,'Livres locaux',stats.get('local_books',0),'success',
                     'Ouvrir la bibliothèque de cette identité',
                     lambda _b,iid=row['identity_id']:controller.open(iid))
                tile(globals_grid,'Emprunts connus',loans if loans is not None else '—','accent')
                tile(globals_grid,"Droits d’achat distants",stats.get('remote_owned_rights',0),'accent')

                breakdown=section(box,'Catalogues',3,'view-list-symbolic')
                support=getattr(host,'supports_identity_view_filters',None)
                filters=bool(callable(support) and support() is True and callable(getattr(host,'open_identity_view',None)))
                names={'paris':'Paris','kobo':'Kobo','gallica':'Gallica','gutenberg':'Gutenberg','bnf-catalogue':'BnF','local':'Local / origine non rapprochée'}
                source_rows=sorted((stats.get('sources') or {}).items())
                if source_rows:
                    for provider,count in source_rows:
                        pname=names.get(provider,provider)
                        callback=(lambda _b,iid=row['identity_id'],pid=provider:controller.filter(iid,'library',pid)) if filters else None
                        tile(breakdown,pname,count,'accent','Ouvrir la bibliothèque filtrée sur '+pname if filters else None,callback)
                else:
                    tile(breakdown,'Catalogues','Aucune donnée','dim-label')
            else:
                unavailable=section(box,'En bref',3,'view-grid-symbolic');tile(unavailable,'Statistiques','Core029 requis','warning')

            groups=source_groups(row,public_providers)
            support=getattr(host,'supports_identity_view_filters',None)
            source_filters=bool(callable(support) and support() is True and callable(getattr(host,'open_identity_view',None)))

            if groups['local']:
                local_grid=section(box,'Local',3,'folder-symbolic')
                for source in groups['local']:
                    pname=str(source.get('label') or source['provider_id'])
                    count=source.get('count')
                    value=count if count is not None else 'Disponible'
                    callback=(lambda _b,iid=row['identity_id'],pid=source['provider_id']:controller.filter(iid,'library',pid)) if source_filters else None
                    tile(local_grid,pname,value,'success',
                         'Ouvrir la bibliothèque locale' if source_filters else None,callback)

            if groups['public']:
                public_grid=section(box,'Sources publiques',3,'system-search-symbolic')
                for source in groups['public']:
                    pname=str(source.get('label') or source['provider_id'])
                    count=source.get('count')
                    value=count if count is not None else 'Disponible'
                    callback=(lambda _b,iid=row['identity_id'],pid=source['provider_id']:controller.filter(iid,'search',pid)) if source_filters else None
                    tile(public_grid,pname,value,'accent',
                         'Rechercher dans cette source publique' if source_filters else None,callback)

            if groups['authenticated']:
                sources_grid=section(box,'Sources authentifiées',3,'network-server-symbolic')
                for linked in groups['authenticated']:
                    connected=bool(linked.get('connected'));pname=str(linked.get('label') or linked['provider_id'])
                    callback=(lambda _b,iid=row['identity_id'],pid=linked['provider_id']:controller.filter(iid,'search',pid)) if source_filters else None
                    settings_open=getattr(host,'open_plugin_settings',None)
                    settings_callback=(lambda _b,pid='provider.'+str(linked['provider_id']):settings_open(pid)) if callable(settings_open) else None
                    tile(sources_grid,pname,'Connectée' if connected else 'À connecter','success' if connected else 'warning',
                         'Rechercher dans cette source' if source_filters else None,callback,settings_callback)

            sync=row.get('sync') or {}
            actions_grid=section(box,'Synchronisation',4,'view-refresh-symbolic')
            tile(actions_grid,'Acquisitions',sync.get('new_acquisitions',0),'success')
            tile(actions_grid,'Actions requises',sync.get('attention_required',0),'warning')
            tile(actions_grid,'Erreurs',sync.get('errors',0),'error' if sync.get('errors') else 'dim-label')
            tile(actions_grid,'État',STATUS.get(sync.get('status'),'Jamais synchronisée'),
                 'success' if sync.get('status')=='success' else ('warning' if sync.get('status') in {'running','partial'} else ('error' if sync.get('status')=='failed' else 'dim-label')))

            attention=attention_items(row)
            if attention:
                attention_grid=section(box,'À traiter',3,'dialog-warning-symbolic')
                for action in attention:
                    count=action.get('count')
                    value=count if count is not None else 'Action requise'
                    tile(attention_grid,action['label'],value,action.get('kind') or 'warning')

            info_grid=section(box,'Profil et appareils',6,'preferences-system-symbolic')
            loans_missing=isinstance(stats,dict) and stats.get('known_loans') is None
            primary_span=2 if loans_missing else 3
            if isinstance(stats,dict):
                tile(info_grid,'Emplacements',stats.get('local_placements',0),'accent',span=primary_span)
                if loans_missing:tile(info_grid,'Prêts','Non actualisés','warning',span=2)
            tile(info_grid,'Compte ADE','Profil lié' if row.get('adept_profile_id') else 'À configurer','success' if row.get('adept_profile_id') else 'warning',span=primary_span)
            tile(info_grid,'Liseuses',readers_text(row).removeprefix('Liseuses : '),'accent',span=3)
            if row.get('deep_last_at'):
                from datetime import datetime
                try:date=datetime.fromtimestamp(int(row['deep_last_at'])).astimezone().strftime('%d/%m/%Y %H:%M')
                except (TypeError,ValueError,OverflowError,OSError):date='Date inconnue'
                tile(info_grid,'Vérification complète',date,'dim-label')
            if isinstance(stats,dict):
                tile(info_grid,'Chiffres','Dernier inventaire','dim-label','Les sources peuvent se chevaucher ; les comptes de prêts peuvent être partiels.',span=3)

            if sync.get('errors') or sync.get('attention_required'):
                issues=Gtk.Expander(label='Erreurs et actions')
                message=Gtk.Label(label=issues_text(sync),xalign=0);message.set_wrap(True)
                message.set_wrap_mode(Pango.WrapMode.WORD_CHAR);message.set_width_chars(1);message.set_max_width_chars(44);message.set_hexpand(True)
                issues.set_child(message);box.append(issues)
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
