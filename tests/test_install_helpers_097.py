import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from cifereca import dev_updater as dev, updater
from cifereca.install_paths import ArchiveNotFoundError, downloads_dir, resolve_archive


class Paths(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.cwd=self.path/'cwd';self.cwd.mkdir();self.config=self.path/'config';self.config.mkdir()
        self.downloads=self.path/'Téléchargements bureau';self.downloads.mkdir()
        (self.config/'user-dirs.dirs').write_text('XDG_DOWNLOAD_DIR="'+str(self.downloads)+'"\n')
        self.previous=Path.cwd();os.chdir(self.cwd)
        self.env=patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.config)});self.env.start()
    def tearDown(self):self.env.stop();os.chdir(self.previous);self.tmp.cleanup()
    def test_cwd_wins_even_with_same_filename_in_downloads(self):
        (self.cwd/'a.zip').write_bytes(b'cwd');(self.downloads/'a.zip').write_bytes(b'downloads')
        self.assertEqual(resolve_archive('a.zip'),self.cwd/'a.zip')
    def test_localized_downloads_and_spaces(self):
        (self.downloads/'a zip.zip').write_bytes(b'data')
        self.assertEqual(resolve_archive('a zip.zip'),self.downloads/'a zip.zip')
    def test_explicit_relative_path_does_not_fallback(self):
        (self.downloads/'a.zip').write_bytes(b'data')
        with self.assertRaises(ArchiveNotFoundError):resolve_archive('./a.zip')
    def test_missing_lists_both_locations(self):
        with self.assertRaises(ArchiveNotFoundError) as error:resolve_archive('missing.zip')
        self.assertIn(str(self.cwd/'missing.zip'),str(error.exception));self.assertIn(str(self.downloads/'missing.zip'),str(error.exception))
    def test_explicit_absolute_path_keeps_meaning(self):
        file=self.path/'outside.zip';file.write_bytes(b'data');self.assertEqual(resolve_archive(file),file)
    def test_home_variable_expansion(self):
        (self.config/'user-dirs.dirs').write_text('XDG_DOWNLOAD_DIR="$HOME/Téléchargements"\n')
        with patch('cifereca.install_paths.Path.home',return_value=self.path):self.assertEqual(downloads_dir(),self.path/'Téléchargements')
    def test_no_configuration_uses_standard_downloads(self):
        (self.config/'user-dirs.dirs').unlink()
        with patch('cifereca.install_paths.Path.home',return_value=self.path):self.assertEqual(downloads_dir(),self.path/'Downloads')
    def test_shell_substitution_never_executed(self):
        marker=self.path/'executed'
        (self.config/'user-dirs.dirs').write_text('XDG_DOWNLOAD_DIR="$(touch '+str(marker)+')"\n')
        self.assertNotEqual(downloads_dir(),marker);self.assertFalse(marker.exists())
    def test_core_updater_finds_downloads_before_extracting(self):
        file=self.downloads/'core.zip';file.write_bytes(b'fixture')
        with patch.object(updater,'_extract_release',return_value=self.path) as extract,patch.object(updater,'install_source',return_value={'ok':True}):
            self.assertTrue(updater.install_archive('core.zip',dry_run=True)['ok'])
        self.assertEqual(extract.call_args.args[0],file)
    def test_core_missing_reports_update_error(self):
        with self.assertRaisesRegex(updater.UpdateError,'Emplacements essayés'):updater.install_archive('missing.zip')

    def test_plugin_cli_resolves_bare_archive_from_downloads(self):
        import cifereca.cli as cli
        archive=self.downloads/'plugin.zip';archive.write_bytes(b'fixture')
        manager=unittest.mock.MagicMock()
        manifest=unittest.mock.MagicMock(); manifest.plugins=[]
        installed=unittest.mock.MagicMock()
        installed.manifest.package.id='pkg.test'; installed.manifest.package.version='1.0'
        installed.sha256='abc'
        manager.inspect_archive.return_value=manifest
        manager.install.return_value=installed
        manager.packages.active_version.return_value='1.0'
        core=unittest.mock.MagicMock(); core.plugins=manager; core.library=unittest.mock.MagicMock()
        with patch.object(cli.CiferecaCore,'open',return_value=core):
            self.assertEqual(cli.main(['plugin','install','plugin.zip']),0)
        manager.inspect_archive.assert_called_once_with(archive)
        manager.install.assert_called_once_with(archive,activate=True,replace=False)


class PluginsOnly(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.store=self.path/'packages';self.store.mkdir()
        self.store_patch=patch.object(dev,'_plugin_store_root',return_value=self.store);self.store_patch.start()
        self.state_patch=patch.object(dev,'state_dir',return_value=self.path/'state');self.state_patch.start()
    def tearDown(self):self.state_patch.stop();self.store_patch.stop();self.tmp.cleanup()
    def snapshot(self,*,sha=None,plugins=None,core=None,snapshot_id='test'):
        raw=self.path/'package.zip'
        with zipfile.ZipFile(raw,'w') as z:
            z.writestr('cifereca-plugin.json',json.dumps({'manifest':1,'package':{'id':'test.provider','version':'1.0.0','publisher':'test'},'plugins':[{'id':'test.provider','type':'ui.page','entrypoint':'plugin:PLUGIN'}]}))
            z.writestr('plugin.py','PLUGIN=object()')
        manifest={'format':1,'kind':'cifereca-dev-snapshot','id':snapshot_id,'plugins':plugins if plugins is not None else [{'path':'plugins/p.zip','sha256':sha or hashlib.sha256(raw.read_bytes()).hexdigest()}]}
        if core:manifest['core']='core/c.zip'
        archive=self.path/'snapshot.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('snapshot/cifereca-dev.json',json.dumps(manifest));z.write(raw,'snapshot/plugins/p.zip')
            if core:z.writestr('snapshot/core/c.zip',b'core')
        return archive
    def test_dry_run_without_core_has_no_install_or_backup(self):
        with patch.object(updater,'install_archive') as core,patch.object(dev,'_install_plugin_replace') as install,patch.object(dev,'_backup_plugins') as backup:
            result=dev.apply_snapshot(self.snapshot(),dry_run=True)
        self.assertIsNone(result['core']);self.assertFalse(result['core_changed']);core.assert_not_called();install.assert_not_called();backup.assert_not_called()
    def test_success_without_core_never_switches_or_rolls_back_core(self):
        with patch.object(updater,'install_archive') as core,patch.object(updater,'rollback') as rollback,patch.object(dev,'_install_plugin_replace') as install,patch.object(dev,'_smoke_plugins',return_value={'plugin_count':1}):
            result=dev.apply_snapshot(self.snapshot())
        self.assertFalse(result['core_changed']);core.assert_not_called();rollback.assert_not_called();install.assert_called_once()
    def test_failed_plugin_install_restores_original_and_keeps_other_package(self):
        for name in ('test.provider','untouched'):
            directory=self.store/name;directory.mkdir();(directory/'original').write_text('keep')
        def corrupt(*args,**kwargs):
            (self.store/'test.provider/original').write_text('changed');raise dev.DevUpdateError('failed')
        with patch.object(dev,'_install_plugin_replace',side_effect=corrupt),patch.object(updater,'install_archive') as core,patch.object(updater,'rollback') as rollback:
            with self.assertRaises(dev.DevUpdateError):dev.apply_snapshot(self.snapshot())
        self.assertEqual((self.store/'test.provider/original').read_text(),'keep');self.assertEqual((self.store/'untouched/original').read_text(),'keep');core.assert_not_called();rollback.assert_not_called()
    def test_failed_smoke_removes_new_package(self):
        def install(*args,**kwargs):(self.store/'test.provider').mkdir()
        with patch.object(dev,'_install_plugin_replace',side_effect=install),patch.object(dev,'_smoke_plugins',side_effect=dev.DevUpdateError('smoke failed')):
            with self.assertRaises(dev.DevUpdateError):dev.apply_snapshot(self.snapshot())
        self.assertFalse((self.store/'test.provider').exists())
    def test_wrong_hash_rejected_before_any_mutation(self):
        with patch.object(dev,'_backup_plugins') as backup:
            with self.assertRaisesRegex(dev.DevUpdateError,'SHA-256'):dev.apply_snapshot(self.snapshot(sha='bad'))
        backup.assert_not_called()
    def test_empty_snapshot_rejected(self):
        with self.assertRaisesRegex(dev.DevUpdateError,'vide'):dev.apply_snapshot(self.snapshot(plugins=[]),dry_run=True)
    def test_null_plugins_rejected(self):
        archive=self.snapshot(plugins=False)
        with self.assertRaisesRegex(dev.DevUpdateError,'liste plugins'):dev.apply_snapshot(archive,dry_run=True)
    def test_existing_inactive_package_stays_inactive(self):
        directory=self.store/'test.provider';directory.mkdir();(directory/'current.json').write_text('{"active":null}')
        with patch.object(dev,'_install_plugin_replace') as install,patch.object(dev,'_smoke_plugins',return_value={'plugin_count':0}):dev.apply_snapshot(self.snapshot())
        self.assertFalse(install.call_args.kwargs['activate'])
    def test_snapshot_id_cannot_escape_backup_directory(self):
        with patch.object(dev,'_install_plugin_replace'),patch.object(dev,'_smoke_plugins',return_value={}):result=dev.apply_snapshot(self.snapshot(snapshot_id='../../escape'))
        self.assertEqual(Path(result['backup']).parent,self.path/'state/dev-updates')
    def test_mixed_snapshot_failure_rolls_back_core(self):
        old=self.path/'old';old.mkdir()
        with patch.object(updater,'_symlink_target',return_value=old),patch.object(updater,'install_archive',return_value={}),patch.object(updater,'rollback') as rollback,patch.object(dev,'_install_plugin_replace',side_effect=dev.DevUpdateError('failed')):
            with self.assertRaises(dev.DevUpdateError):dev.apply_snapshot(self.snapshot(core=True))
        rollback.assert_called_once_with('old')
    def test_restore_failure_is_explicit(self):
        with patch.object(dev,'_install_plugin_replace',side_effect=dev.DevUpdateError('failed')),patch.object(dev,'_restore_plugins',side_effect=OSError('cannot restore')):
            with self.assertRaisesRegex(dev.DevUpdateError,'restauration incomplète'):dev.apply_snapshot(self.snapshot())

    def test_smoke_rejects_error_in_updated_package(self):
        response=type('Response',(),{'returncode':0,'stdout':json.dumps([{'package_id':'test.provider','plugin_id':'test.plugin','state':'error'}]),'stderr':''})()
        with patch.object(dev.subprocess,'run',return_value=response):
            with self.assertRaisesRegex(dev.DevUpdateError,'contributions installées'):dev._smoke_plugins(['test.provider'])
    def test_smoke_allows_expected_blocked_runtime_and_unrelated_error(self):
        response=type('Response',(),{'returncode':0,'stdout':json.dumps([{'package_id':'test.provider','plugin_id':'test.plugin','state':'blocked'},{'package_id':'other','plugin_id':'other','state':'error'}]),'stderr':''})()
        with patch.object(dev.subprocess,'run',return_value=response):self.assertEqual(dev._smoke_plugins(['test.provider'])['plugin_count'],2)


    def test_smoke_refuses_successful_install_with_missing_active_package(self):
        # CLI exits 0, but package discovery silently omitted the active
        # updated package; this must trigger rollback in apply_snapshot().
        response = type('Response', (), {
            'returncode': 0,
            'stdout': json.dumps([{'package_id': 'unrelated', 'plugin_id': 'other', 'state': 'ready'}]),
            'stderr': '',
        })()
        with patch.object(dev.subprocess, 'run', return_value=response), \
             patch.object(dev, '_package_is_active', return_value=True):
            with self.assertRaisesRegex(dev.DevUpdateError, 'packages actifs absents'):
                dev._smoke_plugins(['test.provider'])

    def test_smoke_refuses_active_package_missing_even_if_cli_list_empty(self):
        response = type('Response', (), {'returncode': 0, 'stdout': '[]', 'stderr': ''})()
        with patch.object(dev.subprocess, 'run', return_value=response), \
             patch.object(dev, '_package_is_active', return_value=None):
            # Package identifiers must never enter a public smoke failure.
            with self.assertRaisesRegex(dev.DevUpdateError, 'packages actifs absents'):
                dev._smoke_plugins(['test.provider'])

    def test_smoke_allows_intentionally_inactive_package_not_in_inventory(self):
        response = type('Response', (), {'returncode': 0, 'stdout': '[]', 'stderr': ''})()
        with patch.object(dev.subprocess, 'run', return_value=response), \
             patch.object(dev, '_package_is_active', return_value=False):
            self.assertEqual(dev._smoke_plugins(['test.provider']), {'plugin_count': 0})

    def test_smoke_requires_every_active_package_in_multi_package_update(self):
        response = type('Response', (), {
            'returncode': 0,
            'stdout': json.dumps([{'package_id': 'test.provider', 'plugin_id': 'test.one', 'state': 'ready'}]),
            'stderr': '',
        })()
        with patch.object(dev.subprocess, 'run', return_value=response), \
             patch.object(dev, '_package_is_active', return_value=True):
            # Missing package IDs must not leak through public smoke exceptions.
            with self.assertRaisesRegex(dev.DevUpdateError, 'packages actifs absents'):
                dev._smoke_plugins(['test.provider', 'other.provider'])


class DesktopExportDirectory(unittest.TestCase):
    """A user-selected export stays in their hands; only the initial folder changes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "config"
        self.cfg.mkdir()
        self.localized = self.root / "Téléchargements personnalisés"
        self.localized.mkdir()
        (self.cfg / "user-dirs.dirs").write_text(
            'XDG_DOWNLOAD_DIR="' + str(self.localized) + '"\n', encoding="utf-8",
        )

    def _choose(self, dialog):
        import sys
        from types import SimpleNamespace
        from cifereca.plugin_api import prefer_downloads_for_save

        fake_gio = SimpleNamespace(File=SimpleNamespace(new_for_path=lambda p: ("gio", p)))
        fake_gi = SimpleNamespace(repository=SimpleNamespace(Gio=fake_gio))
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.cfg)}):
            with patch.dict(sys.modules, {"gi": fake_gi, "gi.repository": fake_gi.repository}):
                return prefer_downloads_for_save(dialog)

    def test_localized_xdg_folder_is_suggested_without_choosing_a_file(self):
        from unittest.mock import Mock
        dialog = Mock()
        self.assertTrue(self._choose(dialog))
        dialog.set_current_folder.assert_called_once_with(("gio", str(self.localized)))
        dialog.set_file.assert_not_called()

    def test_fallback_to_existing_home_downloads_if_xdg_missing(self):
        from unittest.mock import Mock
        (self.cfg / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="/nonexistent/not-here"\n')
        home = self.root / "home"
        (home / "Downloads").mkdir(parents=True)
        dialog = Mock()
        with patch("cifereca.install_paths.Path.home", return_value=home):
            self.assertTrue(self._choose(dialog))
        dialog.set_current_folder.assert_called_once_with(("gio", str(home / "Downloads")))

    def test_no_destination_folder_keeps_native_default(self):
        from unittest.mock import Mock
        (self.cfg / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="/nonexistent/not-here"\n')
        home = self.root / "home"
        home.mkdir()
        dialog = Mock()
        with patch("cifereca.install_paths.Path.home", return_value=home):
            self.assertFalse(self._choose(dialog))
        dialog.set_current_folder.assert_not_called()

    def test_missing_setter_is_non_blocking(self):
        self.assertFalse(self._choose(object()))

    def test_all_user_save_dialog_surfaces_use_desktop_default(self):
        base = Path(__file__).resolve().parents[1]
        cases = {
            "src/cifereca/builtin_ui/system_page.py": 1,
            "src/cifereca/gui/settings_diagnostic_actions.py": 1,
            "src/cifereca/gui/settings_performance_actions.py": 1,
            "src/plugins/diagnostics-page/diagnostics_support.py": 1,
            "src/plugins/browser-recorder/browser_recorder_review_ui.py": 3,
            "src/plugins/epub_optimizer/cifereca_epub_optimizer/ui.py": 1,
        }
        for name, expected in cases.items():
            with self.subTest(path=name):
                contents = (base / name).read_text(encoding="utf-8")
                self.assertEqual(contents.count("prefer_downloads_for_save("), expected)
