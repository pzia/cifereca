import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cifereca.models import Account
from cifereca.gui.shell_model import resolve_pages, delegated_accounts
from cifereca.plugin_api import UiPageContribution
from cifereca.system_status import installation_date, system_status
from cifereca.builtin_ui.cifereca_builtin_ui import SystemPages

class Iteration097(unittest.TestCase):
    def test_usage_precedes_configuration_even_alphabetically(self):
        pages=[UiPageContribution('config','Config','',0,lambda h:None,'configuration'),
               UiPageContribution('search','Search','',99,lambda h:None,'usage')]
        self.assertEqual([p.route for p in resolve_pages(pages)],['search','config'])
    def test_without_delegation_selector_is_empty(self):
        self.assertEqual(delegated_accounts([Account(identity_id='a')],'a'),[])
    def test_primary_and_delegates_exclude_public_and_other_identity(self):
        main=Account(identity_id='a'); child=Account(identity_id='a',is_primary=False)
        public=Account(identity_id='a',provider_id='gallica',is_primary=False)
        other=Account(identity_id='b',is_primary=False)
        self.assertEqual(delegated_accounts([main,child,public,other],'a',{'gallica'}),[main,child])
    def test_public_delegate_does_not_show_selector(self):
        self.assertEqual(delegated_accounts([Account(identity_id='a'),Account(identity_id='a',provider_id='gallica',is_primary=False)],'a',{'gallica'}),[])
    def test_unknown_or_invalid_date_never_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)
            self.assertIsNone(installation_date(path))
            for value in [[],{'installed_at':'yesterday'},{'installed_at':'2026-10-03T05:00:00'}]:
                (path/'.cifereca-install.json').write_text(json.dumps(value))
                self.assertIsNone(installation_date(path))
    def test_timestamp_read(self):
        with tempfile.TemporaryDirectory() as d:
            date='2026-10-03T05:00:00+00:00'
            (Path(d)/'.cifereca-install.json').write_text(json.dumps({'installed_at':date}))
            self.assertEqual(installation_date(d),date)
    def test_system_plugin_is_technical(self):
        page=SystemPages().pages()[0]
        self.assertEqual((page.route,page.group),('system-status','configuration'))
    def test_status_counts_and_builtin_date(self):
        class Manager:
            def diagnostic(self):return [dict(state='ready',enabled=True,builtin=True),dict(state='blocked',enabled=True),dict(state='disabled',enabled=False)]
        with patch('cifereca.system_status.core_installation_date',return_value='date'):
            data=system_status(Manager())
        self.assertEqual((data['ready'],data['attention']),(1,1))
        self.assertEqual(data['plugins'][0]['installed_at'],'date')
    def test_formats_match_mime_without_guessing_catalogue_codes(self):
        from cifereca.gui.query_model import QueryState
        from cifereca.core.search import file_format
        rows=[{'key':'a','media_format':'application/epub+zip'}, {'key':'b','media_format':'MONE'}, {'key':'c','media_format':'PDF'}, {'key':'d','media_format':'EXTL'}]
        self.assertEqual([r['key'] for r in QueryState(types=('epub',)).filter(rows)],['a'])
        self.assertEqual([r['key'] for r in QueryState(types=('pdf',)).filter(rows)],['c'])
        for code in ['EXTL','MONE','MONO']:
            self.assertEqual(file_format(code),'')
    def test_collapsing_only_changes_target_group_and_remembers_state(self):
        import ast
        from types import SimpleNamespace
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
        tree=ast.parse(source.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_toggle_nav_group')
        ns={};exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),'exec'),ns)
        class Row:
            def set_visible(self,v):self.visible=v
        a=Row();b=Row();a.visible=b.visible=True
        host=SimpleNamespace(_nav_group_expanded={},_nav_groups={'a':'usage','b':'configuration'},nav_rows={'a':a,'b':b})
        ns['_toggle_nav_group'](host,'configuration',False)
        self.assertTrue(a.visible);self.assertFalse(b.visible)
        self.assertFalse(host._nav_group_expanded['configuration'])
        ns['_toggle_nav_group'](host,'configuration',True)
        self.assertTrue(b.visible)
