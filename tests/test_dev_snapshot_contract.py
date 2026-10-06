"""Dev-kit contract: core-only snapshots, validation and targeted rollback."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from cifereca import dev_updater as dev


class SnapshotContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def snapshot(self,**changes):
        core=b'core fixture'
        manifest={'format':1,'kind':'cifereca-dev-snapshot','id':'test-core-only',
                  'core':'core/core.zip','core_sha256':hashlib.sha256(core).hexdigest(),'plugins':[]}
        manifest.update(changes);archive=self.path/'snapshot.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('snapshot/cifereca-dev.json',json.dumps(manifest))
            z.writestr('snapshot/core/core.zip',core)
        return archive
    def test_core_only_dry_run_never_changes_plugins(self):
        with patch.object(dev.updater,'install_archive',return_value={'version':'0.9.5+dev.2'}) as install,patch.object(dev,'_backup_plugins') as backup,patch.object(dev,'_install_plugin_replace') as plugins:
            result=dev.apply_snapshot(self.snapshot(),dry_run=True)
        self.assertEqual(result['plugins'],[]);self.assertTrue(result['dry_run'])
        self.assertTrue(install.call_args.kwargs['dry_run'])
        backup.assert_not_called();plugins.assert_not_called()
    def test_wrong_core_hash_rejected_before_installation(self):
        with patch.object(dev.updater,'install_archive') as install:
            with self.assertRaisesRegex(dev.DevUpdateError,'SHA-256'):
                dev.apply_snapshot(self.snapshot(core_sha256='0'*64))
        install.assert_not_called()
    def test_core_path_cannot_escape_snapshot(self):
        with patch.object(dev.updater,'install_archive') as install:
            with self.assertRaisesRegex(dev.DevUpdateError,'hors snapshot'):
                dev.apply_snapshot(self.snapshot(core='../../outside.zip'))
        install.assert_not_called()
    def test_wrong_kind_rejected(self):
        with self.assertRaisesRegex(dev.DevUpdateError,'type'):
            dev._extract_snapshot(self.snapshot(kind='wrong'),self.path/'out')
    def test_zip_traversal_rejected_before_extraction(self):
        archive=self.snapshot()
        with zipfile.ZipFile(archive,'a') as z:z.writestr('snapshot/../escaped','wrong')
        with self.assertRaisesRegex(dev.DevUpdateError,'chemin interdit'):
            dev._extract_snapshot(archive,self.path/'out')
        self.assertFalse((self.path/'out').exists())
    def test_plugin_rollback_preserves_packages_outside_snapshot(self):
        store=self.path/'store';backup=self.path/'backup'
        for name in ('changed','untouched'):
            target=store/name;target.mkdir(parents=True);(target/'current.json').write_text('original')
        with patch.object(dev,'_plugin_store_root',return_value=store):
            dev._backup_plugins(['changed','new'],backup)
            (store/'changed/current.json').write_text('replacement')
            (store/'new').mkdir();(store/'new/current.json').write_text('new')
            dev._restore_plugins(['changed','new'],backup)
        self.assertEqual((store/'changed/current.json').read_text(),'original')
        self.assertEqual((store/'untouched/current.json').read_text(),'original')
        self.assertFalse((store/'new').exists())
    def test_plugin_disabled_state_retained(self):
        store=self.path/'store';target=store/'provider.test';target.mkdir(parents=True)
        (target/'current.json').write_text(json.dumps({'active':False}))
        with patch.object(dev,'_plugin_store_root',return_value=store):
            self.assertFalse(dev._package_is_active('provider.test'))
            self.assertIsNone(dev._package_is_active('new.package'))

if __name__=='__main__':unittest.main()
