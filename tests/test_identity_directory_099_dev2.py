"""Real disk/SQLite/config migration, collision, recovery and scan invariants."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
import zipfile
from unittest.mock import patch
from cifereca.config import Config
from cifereca.storage import Storage
from cifereca.services import LibraryService
from cifereca.diagnostics import Diagnostics
from cifereca.identities.directory_migration import DirectoryMigration, DirectoryMigrationError, JOURNAL_KEY
from cifereca.core import CiferecaCore
from cifereca.plugin_api import write_bundle_manifest, load_bundle_manifest

def _write_minimal_epub(path: Path, body: bytes = b"<html/>") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", body)


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'EBooks'
        self.config=Config(self.base/'config.json');self.config.set('library.directory',str(self.root))
        self.config.set('identities',[{'id':'stable-id','label':'Ma bibliothèque','directory_name':'ma-bibliotheque'}]);self.config.set('providers.accounts',[]);self.config.save()
        self.storage=Storage(self.base/'db.sqlite');self.service=LibraryService(self.config,self.storage,Diagnostics())
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def book(self,name='roman.epub',body=b'<html/>',directory='ma-bibliotheque'):
        path=self.root/directory/'paris'/name;_write_minimal_epub(path,body);return path
    def engine(self):return DirectoryMigration(self.config,self.storage,self.root)
    def plan(self):
        rows=self.config.identities();rows[0]['directory_name']='hugues';return self.engine().plan(rows)
    def pending(self):
        engine=self.engine();plan=self.plan();engine._write(plan);return engine,plan
    def test_label_only_preserves_directory_and_files(self):
        file=self.book();data=deepcopy(self.config.data);data['identities'][0]['label']='Hugues'
        self.assertEqual(self.service.save_configuration(data)['migrated'],0);self.assertTrue(file.is_file())
        self.assertEqual(self.service.current_identity.directory_name,'ma-bibliotheque')
    def test_move_rewrites_disk_db_backup_and_nested_ledger_then_scans(self):
        file=self.book();self.service.catalog_scan();before=self.storage.placements()[0]
        entry=self.storage.catalog_entries()[0];entry['title']='Titre catalogue';entry['metadata']['description']='Résumé durable';self.storage.catalog_entry_put(entry)
        self.storage.backup_put({'provider_id':'paris','credential_id':'p','item_id':'r','title':'Roman','format':'epub','local_path':str(file),'status':'complete'})
        self.storage.catalog_entry_put(entry) # backup_put also mirrors its title; restore the richer catalog title before migration.
        aid=self.storage.acquisition_put({'provider_id':'paris','credential_id':'p','item_id':'r','identity_id':'stable-id','status':'complete','metadata':{'path':str(file),'nested':{'target_path':str(file)},'title':'ma-bibliotheque/fiction'}})
        result=self.service.change_identity_directory('stable-id','hugues')
        new=self.root/'hugues/paris/roman.epub';self.assertTrue(new.is_file());self.assertFalse((self.root/'ma-bibliotheque').exists())
        self.assertEqual(result['scan']['invalid'],0)
        placement=self.storage.placements()[0];self.assertEqual(placement['placement_id'],before['placement_id']);self.assertEqual(placement['identity_id'],'stable-id');self.assertEqual(placement['path'],str(new));self.assertEqual(placement['metadata']['relative_path'],'hugues/paris/roman.epub')
        self.assertEqual(self.storage.backup_list('paris')[0]['local_path'],str(new))
        meta=self.storage.acquisition_get(aid)['metadata'];self.assertEqual(meta['path'],str(new));self.assertEqual(meta['nested']['target_path'],str(new));self.assertEqual(meta['title'],'ma-bibliotheque/fiction')
        self.assertEqual(self.storage.catalog_entries()[0]['title'],'Titre catalogue');self.assertEqual(self.storage.catalog_entries()[0]['metadata']['description'],'Résumé durable')
        self.service.catalog_scan();self.assertEqual(len(self.storage.placements()),1);self.assertEqual(self.storage.catalog_entries()[0]['placement_id'],before['placement_id'])
        self.assertFalse(self.storage.metadata_get(JOURNAL_KEY));self.assertEqual(self.config.identities()[0]['id'],'stable-id')
    def test_different_same_name_files_are_both_preserved(self):
        self.book(body=b'original');target=self.book(body=b'existing',directory='hugues');existing=target.read_bytes()
        result=self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual(result['collisions'],1);self.assertEqual(target.read_bytes(),existing)
        files=list((self.root/'hugues/paris').glob('*.epub'));self.assertEqual(len(files),2)
        self.assertTrue(any(b'original' in p.read_bytes() for p in files))
    def test_identical_collision_keeps_one_file_and_one_placement(self):
        self.book();self.book(directory='hugues');self.service.catalog_scan()
        # An unassigned existing tree can be adopted; it must not be owned by a different identity.
        with self.storage._connect() as db:db.execute("UPDATE placements SET identity_id=NULL WHERE path LIKE ?",(str(self.root/'hugues')+'/%',))
        result=self.service.change_identity_directory('stable-id','hugues');self.assertEqual(result['collisions'],0)
        self.assertEqual(len(list((self.root/'hugues/paris').glob('*.epub'))),1);self.assertEqual(len(self.storage.placements()),1)
        ids={p['placement_id'] for p in self.storage.placements()}
        self.assertTrue(all(row['placement_id'] in ids for row in self.storage.catalog_entries() if row.get('placement_id')))
    def test_existing_tree_is_merged_recursively(self):
        self.book('one.epub');self.book('two.epub',directory='hugues')
        self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual({p.name for p in (self.root/'hugues/paris').iterdir()},{'one.epub','two.epub'})
    def test_empty_or_absent_source(self):
        (self.root/'ma-bibliotheque/empty').mkdir(parents=True)
        self.service.change_identity_directory('stable-id','hugues');self.assertTrue((self.root/'hugues').is_dir())
        self.assertFalse((self.root/'ma-bibliotheque').exists())
    def test_absent_source_existing_destination_is_adopted_and_scanned(self):
        self.book(directory='hugues');result=self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual(result['scan']['cataloged'],1);self.assertEqual(self.storage.placements()[0]['identity_id'],'stable-id')
    def test_source_symlink_refused_before_copy(self):
        file=self.book();outside=self.base/'outside';outside.write_text('unchanged');(file.parent/'link').symlink_to(outside)
        with self.assertRaises(DirectoryMigrationError):self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual(outside.read_text(),'unchanged');self.assertTrue(file.is_file());self.assertFalse((self.root/'hugues').exists())
    def test_destination_symlink_refused(self):
        self.book();outside=self.base/'outside';outside.mkdir();(self.root/'hugues').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(DirectoryMigrationError):self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual(list(outside.iterdir()),[])
    def test_dot_dot_and_escape_refused(self):
        self.book()
        for name in ['..','.','../other','/tmp/out','bad/name','.hidden','hugues.']:
            with self.assertRaises(DirectoryMigrationError):self.service.change_identity_directory('stable-id',name)
    def test_destination_owned_by_other_identity_is_refused(self):
        self.book();data=deepcopy(self.config.data);data['identities'].append({'id':'alex','label':'Alex','directory_name':'alex'})
        self.service.save_configuration(data)
        with self.assertRaises(DirectoryMigrationError):self.service.change_identity_directory('stable-id','alex')
    def test_disk_failure_preserves_original_config_and_recovery_journal(self):
        original=self.book()
        with patch('cifereca.identities.directory_migration.shutil.copytree',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.service.change_identity_directory('stable-id','hugues')
        self.assertTrue(original.is_file());self.assertEqual(Config(self.config.path).identities()[0]['directory_name'],'ma-bibliotheque')
        self.assertEqual(self.engine().pending()['state'],'planned')
        recovered=LibraryService(Config(self.config.path),Storage(self.storage.path),Diagnostics());self.assertTrue((self.root/'hugues/paris/roman.epub').is_file());self.assertFalse(original.exists());recovered.close()
    def test_db_commit_failure_can_abort_without_removing_originals(self):
        file=self.book()
        with patch.object(self.storage,'commit_directory_migration',side_effect=RuntimeError('DB unavailable')):
            with self.assertRaises(RuntimeError):self.service.change_identity_directory('stable-id','hugues')
        self.assertTrue(file.exists());self.assertTrue((self.root/'hugues/paris/roman.epub').exists())
        result=CiferecaCore.abort_directory_migration(config_path=self.config.path,database_path=self.storage.path)
        self.assertTrue(result['originals_preserved']);self.assertTrue(file.exists());self.assertFalse((self.root/'hugues/paris/roman.epub').exists());self.assertFalse(self.engine().pending())
    def test_config_failure_after_db_commit_recovers_forward(self):
        file=self.book();self.service.catalog_scan()
        with patch.object(self.config,'save',side_effect=OSError('config unavailable')):
            with self.assertRaises(OSError):self.service.change_identity_directory('stable-id','hugues')
        self.assertTrue(file.exists());self.assertEqual(self.engine().pending()['state'],'committed')
        self.assertEqual(self.storage.placements()[0]['path'],str(self.root/'hugues/paris/roman.epub'))
        recovered=LibraryService(Config(self.config.path),Storage(self.storage.path),Diagnostics())
        self.assertEqual(recovered.current_identity.directory_name,'hugues');self.assertFalse(file.exists());self.assertFalse(self.engine().pending());recovered.close()
    def test_scan_failure_leaves_journal_and_recovers_without_duplicate(self):
        self.book();self.service.catalog_scan()
        with patch.object(self.service,'catalog_scan',side_effect=OSError('scan unavailable')):
            with self.assertRaises(OSError):self.service.change_identity_directory('stable-id','hugues')
        self.assertTrue(self.engine().pending())
        recovered=LibraryService(Config(self.config.path),Storage(self.storage.path),Diagnostics());self.assertFalse(self.engine().pending());self.assertEqual(len(self.storage.placements()),1);recovered.close()
    def test_resume_after_crash_during_copy(self):
        self.book();engine,plan=self.pending();stage=self.root/('.cifereca-migration-'+plan['id']);stage.mkdir()
        engine._copy_entry(plan['entries'][0],stage/'0')
        self.assertTrue((self.root/'ma-bibliotheque/paris/roman.epub').exists())
        recovered=LibraryService(Config(self.config.path),Storage(self.storage.path),Diagnostics());self.assertFalse((self.root/'ma-bibliotheque').exists());recovered.close()
    def test_changed_destination_never_overwritten_on_resume(self):
        self.book();engine,plan=self.pending();stage=self.root/('.cifereca-migration-'+plan['id']);stage.mkdir()
        engine._copy_entry(plan['entries'][0],stage/'0');target=self.root/'hugues/paris/roman.epub';target.write_bytes(b'user changed')
        with self.assertRaises(DirectoryMigrationError):engine.run()
        self.assertEqual(target.read_bytes(),b'user changed');self.assertTrue((self.root/'ma-bibliotheque/paris/roman.epub').exists())
    def test_changed_source_before_commit_is_retained(self):
        file=self.book();engine,plan=self.pending();file.write_bytes(b'user changed')
        with self.assertRaises(DirectoryMigrationError):engine.run()
        self.assertEqual(file.read_bytes(),b'user changed');self.assertEqual(self.config.identities()[0]['directory_name'],'ma-bibliotheque')
    def test_bundle_collision_moves_whole_bundle(self):
        folder=self.root/'ma-bibliotheque/gallica/audio';folder.mkdir(parents=True);(folder/'track.mp3').write_bytes(b'original')
        write_bundle_manifest(folder,title='Audio',member_metadata={'track.mp3':{'title':'Piste'}})
        dest=self.root/'hugues/gallica/audio';dest.mkdir(parents=True);(dest/'track.mp3').write_bytes(b'existing')
        write_bundle_manifest(dest,title='Autre',member_metadata={'track.mp3':{'title':'Piste'}})
        result=self.service.change_identity_directory('stable-id','hugues');self.assertEqual(result['collisions'],1)
        for folder in (self.root/'hugues/gallica').iterdir():load_bundle_manifest(folder,verify=True)
        self.assertEqual(len(self.storage.placements()),2);self.assertEqual(result['scan']['invalid'],0)
    def test_resume_mid_cleanup_of_directory_is_safe(self):
        first=self.book('one.epub');second=self.book('two.epub');engine,plan=self.pending();stage=self.root/('.cifereca-migration-'+plan['id']);stage.mkdir()
        for i,entry in enumerate(plan['entries']):engine._copy_entry(entry,stage/str(i))
        self.storage.commit_directory_migration(plan);first.unlink()
        recovered=LibraryService(Config(self.config.path),Storage(self.storage.path),Diagnostics());self.assertFalse(second.exists());self.assertEqual(len(self.storage.placements()),2);recovered.close()
    def test_dry_run_does_not_move_or_publish_config(self):
        file=self.book();result=self.service.change_identity_directory('stable-id','hugues',dry_run=True)
        self.assertEqual(result['migrated'],1);self.assertTrue(file.exists());self.assertFalse((self.root/'hugues').exists());self.assertFalse(self.engine().pending())
    def test_change_root_and_identity_directory_together_is_refused(self):
        file=self.book();data=deepcopy(self.config.data);data['identities'][0]['directory_name']='hugues';data['library']['directory']=str(self.base/'other')
        with self.assertRaises(DirectoryMigrationError):self.service.save_configuration(data)
        self.assertTrue(file.exists())

    def test_sql_error_rolls_back_paths_and_keeps_planned_journal(self):
        file=self.book();self.service.catalog_scan()
        self.storage.backup_put({'provider_id':'paris','credential_id':'p','item_id':'r','title':'Roman','format':'epub','local_path':str(file),'status':'complete'})
        with self.storage._connect() as db:
            db.execute("CREATE TRIGGER fail_backup BEFORE UPDATE ON backup_items BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        with self.assertRaises(Exception):self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual(self.storage.placements()[0]['path'],str(file));self.assertEqual(self.storage.backup_list('paris')[0]['local_path'],str(file))
        self.assertEqual(self.engine().pending()['state'],'planned');self.assertTrue(file.exists())
    def test_lock_busy_prevents_mutation(self):
        from cifereca.locking import InterProcessLock,LockBusyError
        from cifereca.paths import acquisition_lock_file
        file=self.book()
        with InterProcessLock(acquisition_lock_file()):
            with self.assertRaises(LockBusyError):self.service.change_identity_directory('stable-id','hugues')
        self.assertTrue(file.exists());self.assertFalse((self.root/'hugues').exists());self.assertFalse(self.engine().pending())
    def test_abort_preserves_preexisting_identical_destination(self):
        self.book();dest=self.book(directory='hugues');engine,plan=self.pending();engine.abort()
        self.assertTrue(dest.exists());self.assertTrue((self.root/'ma-bibliotheque/paris/roman.epub').exists())
    def test_source_file_destination_directory_collision_is_preserved(self):
        file=self.book();dest=self.root/'hugues/paris/roman.epub';dest.mkdir(parents=True);(dest/'keep.txt').write_text('keep')
        result=self.service.change_identity_directory('stable-id','hugues')
        self.assertEqual(result['collisions'],1);self.assertEqual((dest/'keep.txt').read_text(),'keep');self.assertFalse(file.exists())
    def test_multi_identity_batch_keeps_ids_and_current_selection(self):
        self.book();data=deepcopy(self.config.data);data['identities'].append({'id':'alex','label':'Alex','directory_name':'alex'})
        self.service.save_configuration(data);self.book('other.epub',directory='alex');self.service.select_identity('alex')
        data=deepcopy(self.config.data)
        for row in data['identities']:row['directory_name']={'stable-id':'hugues','alex':'alexandre'}[row['id']]
        result=self.service.save_configuration(data);self.assertEqual(result['migrated'],2);self.assertEqual(self.service.current_identity.identity_id,'alex')
        self.assertTrue((self.root/'hugues/paris/roman.epub').exists());self.assertTrue((self.root/'alexandre/paris/other.epub').exists())

    def test_kepub_collision_preserves_compound_extension_and_format(self):
        self.book('roman.kepub.epub',body=b'original');self.book('roman.kepub.epub',body=b'existing',directory='hugues')
        self.service.change_identity_directory('stable-id','hugues')
        files=list((self.root/'hugues/paris').iterdir())
        self.assertEqual(len(files),2);self.assertTrue(all(p.name.endswith('.kepub.epub') for p in files))
        self.assertTrue(all(row['media_format']=='kepub' for row in self.storage.catalog_entries()))
