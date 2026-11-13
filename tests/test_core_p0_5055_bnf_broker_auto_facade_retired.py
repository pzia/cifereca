"""P0 #5055: Core broker no longer owns supplier-specific auto toggles."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bnf_auto_widget_and_broker_entrypoints_are_absent():
    broker = (ROOT / "src/cifereca/brokers.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(broker)
    methods = {
        item.name
        for node in tree.body if isinstance(node, ast.ClassDef)
        for item in node.body if isinstance(item, ast.FunctionDef)
    }
    assert "bnf_auto_download_enabled" not in methods
    assert "set_bnf_auto_download" not in methods
    assert "_account_bound_for_bnf" not in methods
    assert "source_sync_consent" in methods
    assert "set_source_sync_consent" in methods
    gui = (ROOT / "src/cifereca/gui/sync_controller.py").read_text(
        encoding="utf-8",
    )
    assert "bnf_auto_download_enabled" not in gui
    assert "set_bnf_auto_download" not in gui


def test_historical_source_consent_reader_and_revision_fence_stay_in_core():
    bridge = (ROOT / "src/cifereca/core/source_auto_consent_bridge.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(bridge)
    funcs = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert "legacy_source_auto_policy" in funcs
    assert "legacy_source_auto_current" in funcs
    assert "legacy_source_auto_in_transaction" in funcs
    metadata = (ROOT / "src/cifereca/core/legacy_source_consent_metadata.py").read_text(
        encoding="utf-8",
    )
    # The authoritative persisted key grammar stays in Core, independent of
    # acquisition decision dispatch, and the bridge retains its reader ABI.
    assert '("bnf-pret", "loan")' in metadata
    assert "revision" in bridge
