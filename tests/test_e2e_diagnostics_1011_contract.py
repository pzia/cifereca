"""#1011 independent GTK recipe contract: real Diagnostics widget, not a mock screenshot."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def test_diagnostics_1366_registered_in_existing_e2e_runner():
    spec = importlib.util.spec_from_file_location("cifereca_ux_e2e", ROOT / "e2e/run.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    scenario = module.SCENARIOS["diagnostics-1366"]
    assert scenario["plugin"] == "ui.page.diagnostics"
    assert scenario["runner"] == "gtk-diagnostics"
    assert scenario["fixture"] == "startup"
    assert scenario["width"] == 1366
    assert [name for name, _ in module.select("diagnostics-1366", None)] == ["diagnostics-1366"]


def test_diagnostics_probe_exercises_real_plugin_and_safe_offline_evidence():
    source = (ROOT / "e2e/diagnostics_page_probe.py").read_text(encoding="utf-8")
    for expression in (
        'importlib.import_module("cifereca_diagnostics_page")',
        "plugin._build(host)",
        'plugin.llm is None',
        'broker.checks == 0',
        'find_expander("Ouvrir le rapport support")',
        'find_expander("Détails expurgés")',
        "json.loads(payload)",
        'find_button("Suivant")',
        'broker.event_queries[-1]["offset"] == 50',
        'report["status"] = "PASS"',
        '"GDK_BACKEND"',
    ):
        assert expression in source
    assert "requests.get(" not in source
    assert "httpx.get(" not in source
    assert "CiferecaCore.open(" not in source  # This is a plugin integration probe, not the entire app.
