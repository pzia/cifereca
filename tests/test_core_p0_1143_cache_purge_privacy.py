"""P0 #1143: cache purge failures report fixed codes, not private filesystem data."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import cache_runtime


PRIVATE = "PRIVATE_ACCOUNT_2026 /home/secret/book.acsm token=SIGNED_CACHE_TOKEN"


def test_cache_child_deletion_error_does_not_leak_child_name_or_oserror(
    tmp_path, monkeypatch,
):
    root = tmp_path / "cache"
    item = root / "PRIVATE_ACCOUNT_2026"
    item.mkdir(parents=True)
    (item / "book").write_text("fixture", encoding="utf-8")
    real = cache_runtime.shutil.rmtree

    def hostile_rmtree(path, *args, **kwargs):
        if Path(path) == item:
            raise OSError(PRIVATE)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(cache_runtime.shutil, "rmtree", hostile_rmtree)
    count, size, warnings = cache_runtime._purge_tree(root)
    assert count == 0
    assert size == 0
    assert warnings == ("cache_entry_delete_failed",)
    assert item.is_dir()
    assert PRIVATE not in json.dumps(warnings)
    assert "PRIVATE_ACCOUNT_2026" not in json.dumps(warnings)


def test_cache_root_enumeration_failure_is_code_only(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir()
    original = Path.iterdir

    def forbidden(self):
        if self == root:
            raise OSError(PRIVATE)
        return original(self)

    monkeypatch.setattr(Path, "iterdir", forbidden)
    assert cache_runtime._purge_tree(root) == (
        0, 0, ("cache_enumeration_failed",),
    )


@pytest.mark.parametrize(("scope", "namespace", "expired_only", "warning"), [
    ("local", "plugin:PRIVATE_ACCOUNT_2026", False, "cache_namespace_forbidden"),
    ("global", "strange:PRIVATE_ACCOUNT_2026", False, "cache_namespace_unknown"),
    ("local", "plugin:org.test", True, "cache_expiration_unsupported"),
    ("global", "core:epub-covers", True, "cache_expiration_unsupported"),
])
def test_cache_broker_never_echoes_invalid_or_unsupported_namespace(
    tmp_path, monkeypatch, scope, namespace, expired_only, warning,
):
    monkeypatch.setattr(cache_runtime, "cache_dir", lambda: tmp_path / "cache")
    service = NS(storage=NS())
    broker = cache_runtime.ScopedCacheBroker(
        service, plugin_id="org.test", allow_global=(scope == "global"),
    )
    result = broker.purge(namespace, expired_only=expired_only)
    assert result.ok is False
    assert result.warnings == (warning,)
    assert PRIVATE not in repr(result)
    assert "PRIVATE_ACCOUNT_2026" not in repr(result)
    assert result.entries_removed == result.bytes_freed == 0


def test_clean_cache_purge_preserves_delete_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_runtime, "cache_dir", lambda: tmp_path / "cache")
    folder = tmp_path / "cache" / "plugins" / "org.test"
    folder.mkdir(parents=True)
    (folder / "entry.json").write_text('{"offline": true}', encoding="utf-8")
    broker = cache_runtime.ScopedCacheBroker(NS(storage=NS()), plugin_id="org.test")
    result = broker.purge("plugin:org.test")
    assert result.ok is True
    assert result.entries_removed == 1
    assert result.bytes_freed > 0
    assert result.warnings == ()
    assert not (folder / "entry.json").exists()


@pytest.mark.parametrize("plugin_id", [
    "../outside", "..", ".hidden", "/tmp/private",
    "plugin/subdir", "plugin\\\\outside",
    "https://private.invalid/?token=PRIVATE_ACCOUNT_2026",
])
def test_untrusted_plugin_cache_ids_are_never_interpreted_as_paths(
    plugin_id,
):
    with pytest.raises(ValueError, match="invalid_plugin_cache_namespace"):
        cache_runtime.ScopedCacheBroker(NS(storage=NS()), plugin_id=plugin_id)


def test_global_cache_purge_rejects_escaped_plugin_path_before_mutation(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cache_runtime, "cache_dir", lambda: tmp_path / "cache")
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    (sentinel / "private-book.epub").write_text("preserved", encoding="utf-8")
    broker = cache_runtime.ScopedCacheBroker(
        NS(storage=NS()), plugin_id="core", allow_global=True,
    )
    result = broker.purge("plugin:../../sentinel")
    assert result.ok is False
    assert result.warnings == ("cache_namespace_invalid",)
    assert result.namespaces == ()
    assert (sentinel / "private-book.epub").read_text(encoding="utf-8") == "preserved"


def test_legal_single_component_plugin_id_remains_supported():
    broker = cache_runtime.ScopedCacheBroker(
        NS(storage=NS()), plugin_id="org.cifereca_123-Plugin",
    )
    assert broker._db_namespace == "plugin:org.cifereca_123-Plugin"
