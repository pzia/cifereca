"""#3996: the cache cleanup may only delete namespaces confirmed in its preview."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.builtin_ui.maintenance import CacheMaintenance
from cifereca.cache_runtime import CacheBrokerImpl
from cifereca.gui.maintenance_tools import (
    MaintenanceCancelControl, execute_maintenance_tool, preview_maintenance_tool,
)
from cifereca.plugin_api import CacheNamespaceInfo, CachePurgeResult


def _row(name, *, owner="core", scope="core", entries=1, size_bytes=4, purgeable=True):
    return CacheNamespaceInfo(name, owner, scope, entries, size_bytes, 0, purgeable)


class MutableCache:
    def __init__(self, *rows):
        self.rows = {row.namespace_id: row for row in rows}
        self.calls = []
        self.fail = None

    def namespaces(self):
        return tuple(self.rows.values())

    def purge(self, namespace_id):
        self.calls.append(namespace_id)
        if namespace_id == self.fail:
            return CachePurgeResult(False, warnings=("cache_entry_delete_failed",))
        row = self.rows[namespace_id]
        self.rows[namespace_id] = replace(row, entries=0, size_bytes=0)
        return CachePurgeResult(True, row.entries, row.size_bytes, (namespace_id,))


def _tool(cache):
    tool = CacheMaintenance()
    tool.cache = cache
    return tool


def _execute(tool, plan, control=None):
    return execute_maintenance_tool(
        tool, NS(), plan, control or MaintenanceCancelControl(),
        destructive=False,
    )


def test_preview_A_then_new_namespace_B_only_purges_A():
    cache = MutableCache(_row("core:A"))
    tool = _tool(cache)
    preview = preview_maintenance_tool(tool, NS(), destructive=False)
    cache.rows["core:B"] = _row("core:B", entries=3, size_bytes=50)
    result = _execute(tool, preview)
    assert result.ok
    assert result.changed == 1 and result.facts["bytes_freed"] == 4
    assert result.facts["namespaces"] == ("core:A",)
    assert cache.calls == ["core:A"]
    assert cache.rows["core:B"].entries == 3


@pytest.mark.parametrize("mutation", [
    lambda row: replace(row, owner="another"),
    lambda row: replace(row, scope="plugin"),
    lambda row: replace(row, purgeable=False),
    lambda row: replace(row, entries=2),
    lambda row: replace(row, size_bytes=100),
    lambda row: None,
])
def test_modified_or_missing_confirmed_namespace_refuses_without_mutation(mutation):
    cache = MutableCache(_row("core:A"), _row("core:C"))
    tool = _tool(cache)
    plan = tool.plan(NS())
    changed = mutation(cache.rows["core:A"])
    if changed is None:
        del cache.rows["core:A"]
    else:
        cache.rows["core:A"] = changed
    result = _execute(tool, plan)
    assert not result.ok and "cache_plan_stale" in result.warnings
    assert result.changed == 0 and result.facts["namespaces"] == ()
    assert cache.calls == []
    assert cache.rows["core:C"].entries == 1


def test_empty_and_legacy_plan_never_expand_to_new_namespaces():
    cache = MutableCache(_row("core:empty", entries=0, size_bytes=0))
    tool = _tool(cache)
    plan = tool.plan(NS())
    cache.rows["core:B"] = _row("core:B")
    result = _execute(tool, plan)
    assert result.ok and result.changed == 0 and cache.calls == []
    from cifereca.plugin_api import MaintenancePlan
    legacy = MaintenancePlan("core.cache.cleanup", "legacy")
    rejected = _execute(tool, legacy)
    assert not rejected.ok and rejected.warnings == ("cache_plan_invalid",)
    assert cache.calls == []


def test_partial_failure_and_replay_have_true_counts():
    cache = MutableCache(_row("core:A"), _row("core:B", entries=2, size_bytes=8))
    cache.fail = "core:B"
    tool = _tool(cache)
    plan = tool.plan(NS())
    result = _execute(tool, plan)
    assert not result.ok and result.changed == 1
    assert result.facts == {"bytes_freed": 4, "namespaces": ("core:A",)}
    assert cache.rows["core:B"].entries == 2
    # No replay of a consumed plan, even when the first purge succeeded.
    assert "cache_plan_stale" in _execute(tool, plan).warnings
    assert cache.calls == ["core:A", "core:B"]
    cache.fail = None
    refreshed = tool.plan(NS())
    assert _execute(tool, refreshed).ok
    assert cache.rows["core:B"].entries == 0


def test_cancellation_after_first_namespace_reports_partial_result():
    cache = MutableCache(_row("core:A"), _row("core:B"))
    tool = _tool(cache)
    plan = tool.plan(NS())
    control = MaintenanceCancelControl()
    original = cache.purge
    def cancel_after_first(namespace_id):
        result = original(namespace_id)
        control.cancel()
        return result
    cache.purge = cancel_after_first
    # The second namespace is never deleted, and a partial cancellation
    # is distinguishable from a successful full purge.
    result = _execute(tool, plan, control)
    assert not result.ok and result.changed == 1
    assert "cache_purge_cancelled" in result.warnings
    assert cache.calls == ["core:A"] and cache.rows["core:B"].entries == 1


class _Storage:
    def cache_namespace_stats(self):
        return []

    def cache_delete_namespace_counted(self, *args, **kwargs):
        raise AssertionError("no database purge in filesystem-only test")


def _broker(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    return CacheBrokerImpl(NS(storage=_Storage()))


def test_broker_rejects_unlisted_namespace_and_enforces_plugin_scope(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    root = tmp_path / "xdg-cache" / "cifereca" / "plugins"
    for name in ("a", "b"):
        path = root / name
        path.mkdir(parents=True)
        (path / "entry.bin").write_bytes(b"x")
    untrusted = broker.for_plugin("a", trusted=False)
    assert not untrusted.purge("plugin:b").ok
    assert not broker.purge("db:plugin:ghost").ok
    assert (root / "b" / "entry.bin").exists()
    purged = untrusted.purge("plugin:a")
    assert purged.ok and purged.entries_removed == 1
    assert (root / "b" / "entry.bin").exists()


def test_broker_refuses_nonpurgeable_and_symlink_root(tmp_path, monkeypatch):
    broker = _broker(tmp_path, monkeypatch)
    root = tmp_path / "xdg-cache" / "cifereca" / "plugins"
    root.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "do-not-delete").write_bytes(b"secret")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    result = broker.for_plugin("linked", trusted=False).purge("plugin:linked")
    assert not result.ok and "cache_namespace_invalid" in result.warnings
    assert (outside / "do-not-delete").exists()

    ordinary = root / "ordinary"
    ordinary.mkdir()
    (ordinary / "cache.bin").write_bytes(b"data")
    scoped = broker.for_plugin("ordinary", trusted=False)
    original = type(scoped)._plugin_namespace
    def unavailable(self, plugin_id):
        row = original(self, plugin_id)
        return replace(row, purgeable=False) if row.namespace_id == "plugin:ordinary" else row
    monkeypatch.setattr(type(scoped), "_plugin_namespace", unavailable)
    result = scoped.purge("plugin:ordinary")
    assert not result.ok and "cache_namespace_not_purgeable" in result.warnings
    assert (ordinary / "cache.bin").exists()
