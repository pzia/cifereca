"""P0 #2141: source cards must request the available scroll width.

These checks are headless; GTK visual validation at 1280/1366 px remains
necessary before claiming that the responsive layout is complete.
"""
from pathlib import Path


def test_sync_source_container_expands_without_fixed_width():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/sync_controller.py").read_text()
    assert "outer.set_hexpand(True)" in source
    assert "self._sync_sources_box.set_hexpand(True)" in source
    assert "group.set_hexpand(True)" in source
    assert "outer.append(self._sync_sources_box)" in source
    assert "set_size_request(" not in source


def test_source_actions_remain_account_scoped():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/sync_controller.py").read_text()
    assert "if getattr(source, \"identity_id\", None) != scope.identity_id:" in source
    assert "if not sync_action_scope_current(self, scope):" in source
