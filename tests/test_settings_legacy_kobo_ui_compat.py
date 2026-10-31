"""#3731: provider-specific legacy GTK controls stay outside generic CoreUI."""
from pathlib import Path

from cifereca.gui import (
    settings_sync_section,
    settings_legacy_kobo_section,
    settings_page,
    settings_sync_actions,
)


def test_legacy_kobo_section_exports_are_strict_compatibility_aliases():
    assert settings_sync_section.KoboSyncSettings is settings_legacy_kobo_section.KoboSyncSettings
    assert settings_sync_section.build_kobo_sync_section is settings_legacy_kobo_section.build_kobo_sync_section
    assert settings_sync_section.kobo_backup_action_state is settings_legacy_kobo_section.kobo_backup_action_state


def test_generic_settings_page_uses_extracted_legacy_builder():
    assert settings_page.build_kobo_sync_section is settings_legacy_kobo_section.build_kobo_sync_section
    assert settings_sync_actions.kobo_backup_action_state is settings_legacy_kobo_section.kobo_backup_action_state
    root = Path(__file__).resolve().parents[1] / 'src/cifereca/gui'
    shim = (root / 'settings_sync_section.py').read_text(encoding='utf-8')
    assert 'class KoboSyncSettings:' not in shim
    assert 'def build_kobo_sync_section(' not in shim
    assert 'kobo_backup.enabled' not in shim
    assert 'Gtk.' not in shim
