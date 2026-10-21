"""Root authorization from normalized configuration, without filesystem reads."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cifereca.config import Config
from cifereca.core.library_audit_roots import configured_identity_roots


def config(root, backup='', rows=None):
    value = Config.__new__(Config)
    value.data = {'library': {'directory': str(root)},
                        'kobo_backup': {'directory': backup},
                        'identities': rows or [
                            {'id': 'alice', 'label': 'PRIVATE LABEL', 'directory_name': 'declared-a'},
                            {'id': 'bob', 'label': 'OTHER', 'directory_name': 'declared-b'},
                        ]}
    return value


def test_uses_normalized_declared_directory_without_io_or_label_guessing(tmp_path):
    cfg = config(tmp_path/'absent')
    with patch.object(Path, 'mkdir', side_effect=AssertionError('write')), \
         patch.object(Path, 'resolve', side_effect=AssertionError('resolve')), \
         patch.object(Path, 'exists', side_effect=AssertionError('probe')), \
         patch.object(Path, 'stat', side_effect=AssertionError('probe')):
        assert configured_identity_roots(cfg, 'alice') == {'alice': (tmp_path/'absent'/'declared-a',)}
    assert not (tmp_path/'absent').exists()


def test_custom_kobo_target_is_only_selected_identity_subtree(tmp_path):
    cfg = config(tmp_path/'library', str(tmp_path/'backup'))
    assert configured_identity_roots(cfg, 'alice') == {
        'alice': (tmp_path/'library'/'declared-a', tmp_path/'backup'/'declared-a'/'kobo')}


def test_relative_backup_is_relative_to_library_and_not_home(tmp_path):
    cfg = config(tmp_path/'library', 'local-backups')
    assert configured_identity_roots(cfg, 'bob') == {
        'bob': (tmp_path/'library'/'declared-b', tmp_path/'library'/'local-backups'/'declared-b'/'kobo')}


def test_relative_library_matches_acquisition_home_rule(tmp_path):
    with patch.object(Path, 'home', return_value=tmp_path):
        assert configured_identity_roots(config('Books'), 'alice') == {'alice': (tmp_path/'Books'/'declared-a',)}


def test_environment_expansion_is_configuration_only(tmp_path, monkeypatch):
    monkeypatch.setenv('RECONCILIATION_TEST_ROOT', str(tmp_path/'books'))
    assert configured_identity_roots(config('$RECONCILIATION_TEST_ROOT'), 'alice') == {'alice': (tmp_path/'books'/'declared-a',)}


@pytest.mark.parametrize('root,backup', [('/', ''), ('/books/../private', ''),
                                       ('/books', '../backup'), ('/books', '/'),
                                       ('/books', 123)])
def test_unsafe_root_configuration_fails_closed(root, backup):
    with pytest.raises(ValueError, match='invalid_reconciliation_root_configuration'):
        configured_identity_roots(config(root, backup), 'alice')


def test_backup_nested_in_other_identity_is_not_authorized(tmp_path):
    cfg = config(tmp_path, str(tmp_path/'declared-a'))
    for identity in ('alice', 'bob'):
        with pytest.raises(ValueError, match='reconciliation_identity_root_collision'):
            configured_identity_roots(cfg, identity)


@pytest.mark.parametrize('rows', [
    [{'id': 'alice', 'directory_name': 'same'}, {'id': 'bob', 'directory_name': 'same'}],
    [{'id': 'alice', 'directory_name': 'A'}, {'id': 'alice', 'directory_name': 'B'}],
    [{'id': 'alice', 'directory_name': '../other'}],
    [{'id': 'alice', 'directory_name': 'A/B'}],
    [{'id': 'alice', 'directory_name': '...'}],
    [],
])
def test_malformed_or_colliding_normalized_projection_is_refused(tmp_path, rows):
    cfg = SimpleNamespace(get=lambda key, default=None: str(tmp_path) if key == 'library.directory' else '',
                          identities=lambda: rows)
    with pytest.raises(ValueError):
        configured_identity_roots(cfg, 'alice')


def test_unknown_identity_never_defaults_to_current_or_first(tmp_path):
    with pytest.raises(ValueError, match='unknown_reconciliation_identity'):
        configured_identity_roots(config(tmp_path), 'unknown')
