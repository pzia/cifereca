"""Local filesystem/cache contracts through the real LibraryService facade."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import os

import pytest

import cifereca.services as facade
from cifereca.services import LibraryService


@pytest.fixture
def service(tmp_path, monkeypatch):
    service = LibraryService.__new__(LibraryService)
    service.storage = NS(path=tmp_path / 'state' / 'library.sqlite3')
    service.config = NS(get=Mock(return_value=7))
    forget = Mock()
    logger = NS(info=Mock(), warning=Mock())
    monkeypatch.setattr(facade.acsm_cache, 'forget', forget)
    monkeypatch.setattr(facade, '_TRACE_LOG', logger)
    monkeypatch.setattr(facade.time, 'time', lambda: 2_000_000)
    return service, forget, logger


def staged(service, name='input.acsm', age=0):
    path = service._acquisition_work_dir('alice', 'paris', 'loan-1') / name
    path.write_bytes(b'synthetic ACSM fixture, no Adobe operation')
    os.utime(path, (2_000_000 - age, 2_000_000 - age))
    return path


def test_staging_uses_sanitized_identity_provider_and_loan_components(service, monkeypatch):
    instance, _, _ = service
    seen = []
    def component(value, fallback):
        seen.append((value, fallback))
        return {'alice': 'identity-safe', 'paris': 'provider-safe', 'loan-1': 'loan-safe'}[value]
    monkeypatch.setattr(facade, 'safe_library_component', component)
    path = instance._acquisition_work_dir('alice', 'paris', 'loan-1')
    assert path == instance.storage.path.parent / 'acquisition-work/identity-safe/provider-safe/loan-safe'
    assert path.is_dir()
    assert seen == [('alice', 'default'), ('paris', 'provider'), ('loan-1', 'loan')]


def test_provider_root_without_loan_has_no_extra_component(service):
    instance, _, _ = service
    path = instance._acquisition_work_dir('alice', 'paris')
    assert path == instance.storage.path.parent / 'acquisition-work/alice/paris'
    assert path.is_dir()


def test_success_deletes_only_input_forgets_cache_and_removes_empty_loan_directory(service):
    instance, forget, logger = service
    path = staged(instance)
    ancestor = path.parent.parent
    instance._cleanup_acsm_after_success(path, loan_id='loan-1')
    assert not path.exists() and not path.parent.exists() and ancestor.is_dir()
    forget.assert_called_once_with(path)
    logger.info.assert_called_once()
    logger.warning.assert_not_called()


def test_success_preserves_other_files_and_does_not_touch_library(service):
    instance, forget, _ = service
    path = staged(instance)
    other = path.with_name('other.acsm'); other.write_bytes(b'pending')
    book = instance.storage.path.parent / 'library' / 'book.epub'
    book.parent.mkdir(); book.write_bytes(b'kept book')
    instance._cleanup_acsm_after_success(path, loan_id='loan-1')
    assert other.read_bytes() == b'pending' and book.read_bytes() == b'kept book'
    assert path.parent.is_dir()
    forget.assert_called_once_with(path)


def test_none_input_does_not_touch_cache_or_logger(service):
    instance, forget, logger = service
    instance._cleanup_acsm_after_success(None, loan_id='loan-1')
    forget.assert_not_called(); logger.info.assert_not_called(); logger.warning.assert_not_called()


def test_failed_unlink_keeps_input_cache_and_parent(service, monkeypatch):
    instance, forget, logger = service
    path = staged(instance)
    original = Path.unlink
    def refused(received, *args, **kwargs):
        if received == path: raise PermissionError('synthetic denied')
        return original(received, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', refused)
    instance._cleanup_acsm_after_success(path, loan_id='loan-1')
    assert path.is_file() and path.parent.is_dir()
    forget.assert_not_called(); logger.info.assert_not_called(); logger.warning.assert_called_once()


def test_retention_deletes_at_exact_boundary_but_keeps_new_and_non_acsm_files(service):
    instance, forget, logger = service
    old = staged(instance, 'old.acsm', age=100)
    new = staged(instance, 'new.acsm', age=99)
    book = staged(instance, 'final.epub', age=1000)
    library_input = instance.storage.path.parent / 'library' / 'kept.acsm'
    library_input.parent.mkdir(); library_input.write_bytes(b'kept input')
    assert instance._cleanup_old_acsm_work(max_age_seconds=100) == {'deleted': 1, 'kept': 1}
    assert not old.exists() and new.is_file() and book.is_file() and library_input.is_file()
    forget.assert_called_once_with(old); logger.info.assert_called_once()
    instance.config.get.assert_not_called()


@pytest.mark.parametrize('configured,days', [(None,7), ('invalid',7), ([],7), (0,7), (-1,1), ('2',2)])
def test_retention_defaults_and_minimum_are_preserved(service, configured, days):
    instance, forget, _ = service
    instance.config.get.return_value = configured
    old = staged(instance, 'old.acsm', age=days*86400)
    new = staged(instance, 'new.acsm', age=days*86400-1)
    assert instance._cleanup_old_acsm_work() == {'deleted':1,'kept':1}
    instance.config.get.assert_called_once_with('acquisition.acsm_retention_days',7)
    assert not old.exists() and new.is_file()
    forget.assert_called_once_with(old)


def test_retention_counts_failed_deletions_as_kept_and_does_not_forget(service, monkeypatch):
    instance, forget, _ = service
    path = staged(instance, age=100)
    original = Path.unlink
    def refused(received, *args, **kwargs):
        if received == path: raise PermissionError('synthetic denied')
        return original(received, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', refused)
    assert instance._cleanup_old_acsm_work(max_age_seconds=1) == {'deleted':0,'kept':1}
    assert path.is_file(); forget.assert_not_called()


def test_absent_staging_tree_returns_zero_without_creating_it(service):
    instance, forget, logger = service
    assert instance._cleanup_old_acsm_work() == {'deleted':0,'kept':0}
    assert not instance.storage.path.parent.exists()
    forget.assert_not_called(); logger.info.assert_not_called()


def test_staging_permissions_are_private_even_with_umask_022(service):
    instance, _, _ = service
    old_umask = os.umask(0o022)
    try:
        leaf = instance._acquisition_work_dir('alice', 'paris', 'loan-1')
    finally:
        os.umask(old_umask)
    root = instance.storage.path.parent / 'acquisition-work'
    for directory in (root, root / 'alice', root / 'alice/paris', leaf):
        assert directory.stat().st_mode & 0o777 == 0o700


def test_existing_public_staging_directories_are_tightened_without_losing_files(service):
    instance, _, _ = service
    root = instance.storage.path.parent / 'acquisition-work'
    leaf = root / 'alice/paris/loan-1'
    leaf.mkdir(parents=True)
    token = leaf / 'kept.acsm'
    token.write_bytes(b'private synthetic token')
    for directory in (root, root / 'alice', root / 'alice/paris', leaf):
        directory.chmod(0o755)
    assert instance._acquisition_work_dir('alice', 'paris', 'loan-1') == leaf
    assert token.read_bytes() == b'private synthetic token'
    for directory in (root, root / 'alice', root / 'alice/paris', leaf):
        assert directory.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('level', [0, 1, 2, 3])
def test_staging_rejects_symlink_at_every_level_without_chmod_target(service, tmp_path, level):
    instance, _, _ = service
    root = instance.storage.path.parent / 'acquisition-work'
    parts = ('acquisition-work', 'alice', 'paris', 'loan-1')
    parent = instance.storage.path.parent
    parent.mkdir(parents=True, exist_ok=True)
    for part in parts[:level]:
        parent = parent / part
        parent.mkdir(exist_ok=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    outside.chmod(0o755)
    secret = outside / 'keep.acsm'
    secret.write_bytes(b'private')
    (parent / parts[level]).symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        instance._acquisition_work_dir('alice', 'paris', 'loan-1')
    assert secret.read_bytes() == b'private'
    assert outside.stat().st_mode & 0o777 == 0o755


def test_staging_rejects_symlink_storage_parent(service, tmp_path):
    instance, _, _ = service
    outside = tmp_path / 'outside'
    outside.mkdir()
    parent = instance.storage.path.parent
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        instance._acquisition_work_dir('alice', 'paris', 'loan-1')
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('value', ['..', '../outside', '/tmp/evil', 'other/subdir', ''])
def test_staging_rejects_unsanitized_components(service, monkeypatch, value):
    instance, _, _ = service
    monkeypatch.setattr(facade, 'safe_library_component', lambda _value, _fallback: value)
    with pytest.raises(ValueError, match='invalid_acquisition_stage_component'):
        instance._acquisition_work_dir('alice', 'paris', 'loan-1')
    assert not (instance.storage.path.parent / 'acquisition-work').exists()


def test_retention_refuses_linked_staging_root(service, tmp_path):
    instance, forget, _ = service
    outside = tmp_path / 'outside'
    outside.mkdir()
    private = outside / 'external.acsm'
    private.write_bytes(b'external')
    os.utime(private, (0, 0))
    parent = instance.storage.path.parent
    parent.mkdir(parents=True)
    (parent / 'acquisition-work').symlink_to(outside, target_is_directory=True)
    assert instance._cleanup_old_acsm_work(max_age_seconds=1) == {'deleted': 0, 'kept': 0}
    assert private.read_bytes() == b'external'
    forget.assert_not_called()


def test_retention_ignores_linked_subdirectories_and_token_files(service, tmp_path):
    instance, forget, _ = service
    root = instance.storage.path.parent / 'acquisition-work'
    root.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    external = outside / 'old.acsm'
    external.write_bytes(b'external')
    os.utime(external, (0, 0))
    (root / 'linked').symlink_to(outside, target_is_directory=True)
    (root / 'linked-file.acsm').symlink_to(external)
    own = root / 'own.acsm'
    own.write_bytes(b'own')
    os.utime(own, (0, 0))
    assert instance._cleanup_old_acsm_work(max_age_seconds=1) == {'deleted': 1, 'kept': 1}
    assert external.read_bytes() == b'external'
    assert not own.exists()
    assert (root / 'linked-file.acsm').is_symlink()
    forget.assert_called_once_with(own)
