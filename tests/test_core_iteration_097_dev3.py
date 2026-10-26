import json
import tempfile
import unittest
from pathlib import Path
from cifereca.core import CiferecaCore
from cifereca.core.search import filter_loaded_results
from cifereca.plugin_help import plugin_help
from cifereca.plugins.manifest import parse_manifest
from cifereca.plugins.errors import ManifestError

class Core097Dev3(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.core=CiferecaCore.open(config_path=self.path/'config.json',database_path=self.path/'db')
        self.iid=self.core.library.current_identity.identity_id
    def tearDown(self):
        self.core.close();self.tmp.cleanup()
    def local(self,fmt):
        self.core.storage.catalog_entry_put({'catalog_entry_id':'cat','title':'Book','identity_id':self.iid,'provider_id':'paris','item_id':'1','local_state':'local','media_format':fmt,'metadata':{'path':'book.epub'}})
    def right(self,fmt,state='active'):
        self.core.storage.entitlement_put({'provider_id':'paris','credential_id':'test','identity_id':self.iid,'item_id':'1','entitlement_type':'loan','state':state,'metadata':{'title':'Book','media_format':fmt}})
    def test_local_epub_survives_catalogue_codes_and_repeated_sync(self):
        self.local('epub')
        for code in ['MONE','MONO','EXTL']:
            self.right(code)
            row=self.core.library.library_entries(scan=False)[0]
            self.assertEqual(row['media_format'],'epub');self.assertTrue(row['local'])
            self.assertEqual(row['metadata']['source_media_format'],code)
    def test_local_pdf_survives_remote_epub(self):
        self.local('pdf');self.right('epub')
        self.assertEqual(self.core.library.library_entries(scan=False)[0]['media_format'],'pdf')
    def test_returned_right_keeps_local_format(self):
        self.local('epub');self.right('MONE','returned')
        self.assertEqual(self.core.library.library_entries(scan=False)[0]['media_format'],'epub')
    def test_remote_catalogue_code_is_unknown_and_keeps_provenance(self):
        self.right('MONO');row=self.core.library.library_entries(scan=False)[0]
        self.assertIsNone(row['media_format'])
        self.assertEqual(row['metadata']['source_media_format'],'MONO')
    def test_remote_explicit_mime_is_recognized(self):
        self.right('application/epub+zip')
        self.assertEqual(self.core.library.library_entries(scan=False)[0]['media_format'],'epub')
    def test_kepub_and_epub_share_filter_but_variant_is_kept(self):
        rows=[{'media_format':'kepub'},{'media_format':'epub'},{'media_format':'pdf'}]
        result=filter_loaded_results(rows,content_types=['epub'])
        self.assertEqual([r['media_format'] for r in result],['kepub','epub'])
        self.assertEqual(len(filter_loaded_results(rows,content_types=['kepub'])),2)
    def test_builtin_help_available_in_diagnostics(self):
        for row in self.core.plugins.diagnostic():
            self.assertTrue(row['help_text']);self.assertNotEqual(plugin_help(row),'Aide non fournie.')
    def manifest(self,help_value=None):
        row={'id':'test.page','type':'ui.page','entrypoint':'test:PLUGIN','description':'Legacy help'}
        if help_value is not None:row['help_text']=help_value
        return {'manifest':1,'package':{'id':'test.package','version':'1.0.0'},'plugins':[row]}
    def test_help_additive_and_fallback(self):
        old=parse_manifest(self.manifest()).plugins[0]
        self.assertEqual(plugin_help(old),'Legacy help')
        new=parse_manifest(self.manifest(' Detailed help ')).plugins[0]
        self.assertEqual(plugin_help(new),'Detailed help')
        self.assertEqual(plugin_help({}),'Aide non fournie.')
    def test_invalid_help_is_manifest_error(self):
        with self.assertRaises(ManifestError):parse_manifest(self.manifest(['invalid']))
    def test_artifact_format_repairs_stale_local_catalogue_projection(self):
        import zipfile
        # Targeted indexing proves presence inside the configured library,
        # not an arbitrary file next to the test's SQLite database.
        self.core.config.set('library.directory', str(self.path/'library'))
        file=self.core.library.library_dir()/'book.epub'
        with zipfile.ZipFile(file,'w') as z:
            z.writestr('mimetype','application/epub+zip')
            z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
            z.writestr('book.opf','<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata/></package>')
        result=self.core.library.catalog_index_path(file,title='Book',item_id='1',identity_id=self.iid,provider_id='paris')
        self.assertEqual(result['status'],'indexed')
        row=self.core.storage.catalog_entries()[0];row['media_format']='MONO';self.core.storage.catalog_entry_put(row)
        self.right('MONE')
        self.assertEqual(self.core.library.library_entries(scan=False)[0]['media_format'],'epub')
