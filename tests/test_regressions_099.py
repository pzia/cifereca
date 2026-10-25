"""Search and GTK lifecycle regressions, using real methods without a display."""
from tests.gui_source import gui_source_text
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock
import httpx
from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.search_failures import is_technical_search_error
from cifereca.gui.sync_issues import sync_issues_text
from cifereca.gui.query_model import QueryState,FIELDS
from cifereca.models import SearchResult
from cifereca.descriptions import description_text
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.plugin_api import DetailField, DetailSection, UiBookDetailPresentation

def _service(tmp: str) -> LibraryService:
    root = Path(tmp)
    cfg = Config(root / "config.json")
    cfg.set("library.directory", str(root / "EBooks"))
    cfg.set("identities", [
        {"id": "default", "label": "Hugues", "directory_name": "hugues", "preferences": {"reading_languages": ["fr"]}},
        {"id": "alex", "label": "Alex", "directory_name": "alex", "preferences": {"reading_languages": ["fr"]}},
    ])
    cfg.set("providers.accounts", [])
    return LibraryService(cfg, Storage(root / "db.sqlite3"), Diagnostics())

ROOT=Path(__file__).resolve().parents[1]
def functions(file,names,ns):
    tree=ast.parse(gui_source_text(ROOT/'src/cifereca/gui'/file))
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+selected,type_ignores=[])),file,'exec'),ns)
    return {name:ns[name] for name in names}

def methods(file,cls,names,ns):
    tree=ast.parse(gui_source_text(ROOT/'src/cifereca/gui'/file))
    obj=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    selected=[n for n in obj.body if isinstance(n,ast.FunctionDef) and n.name in names]
    future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
    if '__init__' in names:
        obj.body=selected;obj.decorator_list=[]
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future,obj],type_ignores=[])),file,'exec'),ns)
        return ns[cls]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+selected,type_ignores=[])),file,'exec'),ns)
    return {name:ns[name] for name in names}

class Widget:
    def __init__(self,*args,**kw):self.children=[];self.props=kw;self.selected=0;self.text='';self.active=False
    def append(self,w):self.children.append(w)
    def add(self,w):self.children.append(w)
    def add_suffix(self,w):self.children.append(w)
    def add_css_class(self,_name):pass
    def insert(self,w,*a):self.append(w)
    def get_parent(self):return None
    def connect(self,*a):pass
    def get_selected(self):return self.selected
    def set_selected(self,n):self.selected=n
    def get_text(self):return self.text
    def set_text(self,s):self.text=s
    def values(self):return getattr(self,'_values',())
    def set_values(self,v):self._values=v
    @classmethod
    def new_from_strings(cls,*a):return cls()
    def __getattr__(self,name):
        if name.startswith('set_'):return lambda *values:self.props.__setitem__(name[4:],values[0] if len(values)==1 else values)
        if name=='start':return lambda:None
        raise AttributeError(name)

class SearchRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.service=_service(self.tmp.name)
        self.search=Mock(side_effect=httpx.ReadTimeout('unavailable'))
        self.session=NS(connected=True,capabilities=NS(search=True),provider_id='gallica',search=self.search,descriptor=NS(display_name='Gallica'))
        self.service._sessions={'test':self.session};self.service._provider_enabled_for_identity=lambda _:True
        self.service._search_query_variants=lambda _:['original','looser','last']
        self.service._mirror_remote_results=lambda _:None
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def test_timeout_one_attempt_and_not_cached(self):
        self.assertEqual(self.service.search('original'),[]);self.assertEqual(self.search.call_count,1)
        self.service.search('original');self.assertEqual(self.search.call_count,2)
        self.assertTrue(self.service.last_search_warnings)
    def test_partial_warning_keeps_results_and_skips_loose_retry(self):
        row=SearchResult('id','id','GALLICA','digital','Other',None,None,'EPUB',None,None,'Summary',None,None,'OPDS',source_provider='gallica')
        self.search.side_effect=None;self.search.return_value=[row];self.session.last_search_warnings=['Audio temporarily unavailable']
        self.assertEqual(self.service.search('original'),[row]);self.assertEqual(self.search.call_count,1)
        self.assertEqual(self.service.last_search_warnings, ['Gallica: résultats partiels'])
        self.service.search('original');self.assertEqual(self.search.call_count,2)
    def test_valid_empty_result_is_cached(self):
        self.search.side_effect=None;self.search.return_value=[]
        self.service.search('original');self.assertEqual(self.search.call_count,3)
        self.service.search('original');self.assertEqual(self.search.call_count,3)
    def test_semantic_failure_can_try_looser_query(self):
        self.search.side_effect=[ValueError('unsupported exact query'),[],[]]
        self.service.search('original');self.assertEqual(self.search.call_count,3)
    def test_wrapped_transport_and_http_errors(self):
        wrapper=RuntimeError('wrapped');wrapper.__cause__=httpx.ConnectError('offline')
        self.assertTrue(is_technical_search_error(wrapper))
        response=httpx.Response(503,request=httpx.Request('GET','https://example.test'))
        self.assertTrue(is_technical_search_error(httpx.HTTPStatusError('503',request=response.request,response=response)))
        self.assertFalse(is_technical_search_error(ValueError('query')))

class DigitalDefault(unittest.TestCase):
    def test_new_controls_default_digital_but_changes_stay_in_session(self):
        gtk=NS(Box=Widget,SearchEntry=Widget,ScrolledWindow=Widget,DropDown=Widget,Label=Widget,Align=NS(START=0),Orientation=NS(VERTICAL=1,HORIZONTAL=2),PolicyType=NS(AUTOMATIC=0,NEVER=1))
        def multi(*a):return Widget()
        funcs=methods('query_controls.py','QueryControls',{'__init__','state','set_state'},{'Gtk':gtk,'MultiSelect':multi,'QueryState':QueryState,'FIELDS':FIELDS})
        cls=funcs
        cls._changed=lambda self:None
        for library in [False,True]:
            control=cls([],lambda:None,lambda:None,library=library)
            self.assertEqual(control.state().scope,'digital')
            control.set_state(QueryState('Romans',scope='physical',types=('epub',)))
            self.assertEqual(control.state().scope,'physical')
            fresh=cls([],lambda:None,lambda:None,library=library)
            self.assertEqual(fresh.state().scope,'digital')

class SummaryRegression(unittest.TestCase):
    def test_summary_remains_when_holdings_replace_initial_view(self):
        gtk=NS(Label=Widget,Spinner=Widget,Box=Widget,Button=Widget,Orientation=NS(VERTICAL=1),Align=NS(START=1));adw=NS(PreferencesGroup=Widget,ActionRow=Widget)
        helpers=functions('main_window.py',{'book_detail_presentation','compact_detail_rows','text_or_dash'},{'DetailField':DetailField,'DetailSection':DetailSection,'UiBookDetailPresentation':UiBookDetailPresentation,'detail_metadata_items':lambda _metadata:(), 'safe_detail_value':lambda value:str(value or '')})
        funcs=methods('main_window.py','DetailPanel',{'show_search','show_item_detail','show_presentation','_show_description'},{'Gtk':gtk,'Adw':adw,'file_format':lambda s:str(s or '').lower(),'clear_box':lambda w:w.children.clear(),'description_text':description_text,'book_detail_presentation':helpers['book_detail_presentation'],'compact_detail_rows':helpers['compact_detail_rows'],'text_or_dash':helpers['text_or_dash'],'detail_language':lambda _metadata:'','compact_availability_label':lambda value:value,'document_links':lambda _row:(),'result_row':lambda _item:{},'_commercial_search_usage':lambda _item:'','Pango':NS(WrapMode=NS(WORD_CHAR=0),EllipsizeMode=NS(END=1,NONE=0))})
        service=NS(content_type_for=lambda _: 'ebook',provider_display_name=lambda _: 'Paris',availability_display_label=lambda *_:'Indisponible',can_direct_download=lambda _:False)
        host=NS(_decorate=lambda item:None,_clear_actions=lambda:None,_focused_action_key=lambda:'',content=Widget(),window=NS(service=service),_cancel_meta_request=lambda:None,_cover_and_title=lambda *a:None,_group=lambda *a, **k:None,_direct_download_action=lambda *a:None)
        host._append_content=host.content.append
        host.show_presentation=lambda presentation,description=None:funcs['show_presentation'](host,presentation,description=description)
        host._show_description=lambda text:funcs['_show_description'](host,text)
        item=NS(title='Livre',author=None,thumbnail_url=None,source_provider='paris',medium='digital',type='Livre numérique',format='EPUB',publisher=None,published_at=None,description='Résumé durable.',owned=False,metadata={})
        detail=NS(title='Livre',author=None,thumbnail_url=None,type=None,year=None,holdings=[])
        funcs['show_search'](host,item,loading=True)
        def summaries():return [w.children[0].children[0].props['label'] for w in host.content.children if w.props.get('title')=='Résumé']
        self.assertEqual(summaries(),['Résumé durable.'])
        funcs['show_item_detail'](host,item,detail)
        self.assertEqual(summaries(),['Résumé durable.'])
        detail.description='Résumé plus complet.';funcs['show_item_detail'](host,item,detail)
        self.assertEqual(summaries(),['Résumé plus complet.'])

class NativeChooserLifecycle(unittest.TestCase):
    def setUp(self):
        self.idles=[];self.review=Mock();self.window=NS(_plugin_archive_chooser=None,show_error=Mock())
        gtk=NS(ResponseType=NS(ACCEPT=1))
        self.fn=methods('plugin_manager_page.py','PluginManagerPage',{'_archive_chosen','_review_archive_file'},{'Gtk':gtk,'GLib':NS(idle_add=self.idles.append),'Path':Path})
        self.host=NS(window=self.window,_review_archive_file=self.review,manager=NS(inspect_archive=Mock()))
    def test_accept_reads_file_before_deferred_destroy(self):
        chooser=NS(get_file=Mock(return_value='ZIP'),hide=Mock(),destroy=Mock())
        self.window._plugin_archive_chooser=chooser
        self.fn['_archive_chosen'](self.host,chooser,1)
        self.review.assert_called_once_with('ZIP');chooser.destroy.assert_not_called()
        self.assertIs(self.window._plugin_archive_chooser,chooser)
        self.assertFalse(self.idles[0]());chooser.destroy.assert_called_once();self.assertIsNone(self.window._plugin_archive_chooser)
    def test_cancel_does_not_read_or_review_file(self):
        chooser=NS(get_file=Mock(),hide=Mock(),destroy=Mock());self.window._plugin_archive_chooser=chooser
        self.fn['_archive_chosen'](self.host,chooser,0)
        chooser.get_file.assert_not_called();self.review.assert_not_called();self.idles[0]()
    def test_remote_selection_is_rejected_before_inspection(self):
        self.fn['_review_archive_file'](self.host,NS(get_path=lambda:None))
        self.window.show_error.assert_called_once();self.host.manager.inspect_archive.assert_not_called()
    def test_missing_zip_is_rejected_before_inspection(self):
        self.fn['_review_archive_file'](self.host,NS(get_path=lambda:'/no/such/archive.zip'))
        self.window.show_error.assert_called_once();self.host.manager.inspect_archive.assert_not_called()

class SyncIssues(unittest.TestCase):
    def test_four_errors_show_four_titles_and_redact_secret(self):
        report={'detail':{'acquisition_errors':[{'title':f'Sapiens {i}','status':'retryable_error','error':'Failed https://example.test/file?token=SECRET'} for i in range(1,5)]}}
        text=sync_issues_text(report)
        self.assertEqual(text.count('Sapiens'),4);self.assertNotIn('SECRET',text)
    def test_old_runs_explain_absent_details(self):self.assertIn('ancienne synchronisation',sync_issues_text({}))

class ModernChooserLifecycle(unittest.TestCase):
    def test_success_clears_reference_and_reviews_file(self):
        error_type=type('DialogException',(Exception,),{})
        funcs=methods('plugin_manager_page.py','PluginManagerPage',{'_archive_dialog_finished'},{'GLib':NS(Error=error_type),'Gtk':NS(),'Gio':NS()})
        chooser=NS(open_finish=lambda result:'chosen.zip');review=Mock();host=NS(window=NS(_plugin_archive_chooser=chooser,show_error=Mock()),_review_archive_file=review)
        funcs['_archive_dialog_finished'](host,chooser,object())
        review.assert_called_once_with('chosen.zip');self.assertIsNone(host.window._plugin_archive_chooser)
    def test_dismissal_is_silent_and_releases_reference(self):
        class DialogException(Exception):
            def matches(self,domain,code):return domain=='gtk' and code=='dismissed'
        def finish(result):raise DialogException('dismissed')
        funcs=methods('plugin_manager_page.py','PluginManagerPage',{'_archive_dialog_finished'},{'GLib':NS(Error=DialogException),'Gtk':NS(DialogError=NS(DISMISSED='dismissed'),dialog_error_quark=lambda:'gtk'),'Gio':NS(io_error_quark=lambda:'gio',IOErrorEnum=NS(CANCELLED='cancelled'))})
        chooser=NS(open_finish=finish);review=Mock();host=NS(window=NS(_plugin_archive_chooser=chooser,show_error=Mock()),_review_archive_file=review)
        funcs['_archive_dialog_finished'](host,chooser,object())
        review.assert_not_called();host.window.show_error.assert_not_called();self.assertIsNone(host.window._plugin_archive_chooser)

class PersistedAcquisitionErrors(unittest.TestCase):
    def test_sync_counter_has_one_redacted_cause_per_failed_loan(self):
        from cifereca.models import Account,Loan
        with tempfile.TemporaryDirectory() as tmp:
            service=_service(tmp)
            try:
                service.accounts=[Account(display_name='Paris',unique_id='reader',provider_id='paris',credential_id='p',identity_id='default')]
                loans=[Loan(id=f'l{i}',provider='PNB',medium='digital',record_id=f'r{i}',title=f'Sapiens {i}',source_provider='paris',source_credential='p',source_account_id='reader',identity_id='default',ebook_url='https://example.test/book.acsm',due_at='2099-01-01T00:00:00Z') for i in range(4)]
                service._loans_for_account=lambda *a,**kw:loans
                service._acquire_active_loan_automatically=lambda loan:{'status':'retryable_error','code':'timeout','error':'Failure https://example.test/file?token=SECRET'}
                service.catalog_scan=lambda:{'scanned':0};service.kobo_backup_enabled=lambda:False
                enable_synthetic_paris_opening(service)
                service.opening_sync()
                saved=service.storage.latest_sync_run('default')
                self.assertEqual(saved['errors'],4);self.assertEqual(len(saved['detail']['acquisition_errors']),4)
                self.assertNotIn('SECRET',sync_issues_text(saved))
            finally:service.close()
