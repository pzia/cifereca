from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "e2e/run.py"


def _module():
    spec = importlib.util.spec_from_file_location("cifereca_e2e_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_reference_scenario_is_targetable_by_name_and_domain():
    runner = _module()
    assert [name for name, _ in runner.select("startup", None)] == ["startup"]
    assert [name for name, _ in runner.select(None, "core")] == ["startup"]


def test_reference_scenario_runs_real_cifereca_without_plugins():
    runner = _module()
    scenario = runner.SCENARIOS["startup"]
    assert scenario["args"] == ["--no-plugins"]
    assert scenario["plugin"] == "core"


def test_capture_waits_for_visible_surface_instead_of_fixed_startup_sleep():
    source = (
        ROOT / "environnements/openai-chat-debian/capture_app.py"
    ).read_text(encoding="utf-8")
    assert "--startup-timeout" in source
    assert "GTK application did not expose a non-blank surface before timeout." in source
