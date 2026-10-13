"""Exercise the real headless adapter through MainWindow's public facades."""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from cifereca.gui import library_refresh
from cifereca.task_queue import TaskPriority
from tests.test_core_ui_identity_async_recent import _harness


@pytest.mark.parametrize("scan,covers", [(False, False), (True, False), (True, True)])
def test_library_work_keeps_explicit_cover_scan_policy_and_identity(scan, covers):
    host = _harness()
    host.service.catalog_scan = Mock()
    host.service.library_entries = Mock(return_value=[{"identity_id": "alice"}])
    control = object()
    host.refresh_library(scan, control, refresh_covers=covers)
    request = host.runner.requests[-1]
    # Submitting the adapter does not run service IO on the caller's thread.
    host.service.catalog_scan.assert_not_called()
    host.service.library_entries.assert_not_called()
    assert request.kwargs == {
        "control": control, "priority": TaskPriority.USER_ACTION, "kind": "library",
    }
    assert request.work() == [{"identity_id": "alice"}]
    host.service.library_entries.assert_called_once_with(
        identity_id="alice", scan=scan and not covers,
    )
    if covers:
        host.service.catalog_scan.assert_called_once_with(force_covers=True)
    else:
        host.service.catalog_scan.assert_not_called()


@pytest.mark.parametrize("name,kind", [("refresh_library", "library"), ("refresh_recent", "recent")])
def test_background_refresh_keeps_normal_priority_and_no_control(name, kind):
    host = _harness()
    getattr(host, name)()
    assert host.runner.requests[-1].kwargs == {
        "control": None, "priority": TaskPriority.NORMAL, "kind": kind,
    }


def test_facades_forward_caller_arguments_without_replacing_controls(monkeypatch):
    host = _harness()
    control = object()
    library = Mock()
    recent = Mock()
    monkeypatch.setattr(library_refresh, "refresh_library", library)
    monkeypatch.setattr(library_refresh, "refresh_recent", recent)
    host.refresh_library(scan=True, control=control, refresh_covers=True)
    host.refresh_recent(control=control)
    library.assert_called_once_with(host, True, control, refresh_covers=True)
    recent.assert_called_once_with(host, control)


def test_refresh_adapter_import_does_not_load_gtk_or_main_window():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('refresh adapter imports GTK')
sys.meta_path.insert(0, NoGtk())
from cifereca.gui import library_refresh
assert 'cifereca.gui.main_window' not in sys.modules
assert 'gi' not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code], env=os.environ.copy(),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
