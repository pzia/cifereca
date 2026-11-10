"""Guard path-scoped GTK E2E against regressions in active components."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/e2e-smoke.yml"


def e2e_pull_request_patterns():
    content = WORKFLOW.read_text(encoding="utf-8")
    block = re.search(r"(?m)^  pull_request:\n    paths:\n((?:      - '[^']+'\n)+)", content)
    assert block, "E2E Smoke must retain path-scoped pull_request trigger"
    return tuple(re.findall(r"      - '([^']+)'", block.group(1)))


def test_active_components_trigger_real_gtk_e2e():
    patterns = e2e_pull_request_patterns()
    included = (
        "src/cifereca/brokers.py", "src/cifereca/core/source_materialization.py",
        "src/cifereca/core/search_orchestration.py",
        "src/cifereca/core/loan_local_preparation.py",
        "src/cifereca/core/loan_provider_delivery.py",
        "src/cifereca/core/manual_acsm_import.py",
        "src/cifereca/core/selected_owned_search.py",
        "src/cifereca/core/numilog_owned_download.py",
        "src/cifereca/core/explicit_owned_canonical.py",
        "src/cifereca/core/sync_coordination.py",
        "src/plugins/constellations/constellations_page.py",
        "src/plugins/home-page/cifereca_home.py",
        "src/plugins/home-page/cifereca-plugin.json",
        "src/plugins/recommendations/recommendations.py",
        "src/plugins/provider-bnf-pret/cifereca_bnf_pret.py",
        "src/plugins/provider-bnf-pret/cifereca_bnf_pret_web.py",
        "scripts/ci-install-gtk-e2e-runtime.sh",
        "tests/test_e2e_runner_contract.py",
    )
    for path in included:
        assert (ROOT / path).is_file(), f"Dead active-component fixture: {path}"
        assert any(fnmatchcase(path, pattern) for pattern in patterns), f"{path} must trigger E2E Smoke"

def test_unrelated_work_stays_out_of_heavy_gtk_gate():
    patterns = e2e_pull_request_patterns()
    excluded = (
        "src/plugins/baen/cifereca_baen.py",
        "src/plugins/provider-gutenberg/cifereca_gutenberg.py",
        "src/plugins/provider-numilog/cifereca_numilog.py",
        "src/plugins/provider-bnf-pret/WEB.md",
        "src/plugins/provider-bnf-pret/tests/test_p1_4118_two_cover_pipeline.py",
        "docs/PLUGINS-ONLY-RELEASE.md",
        ".github/workflows/plugins-only-release.yml",
        "README.md",
    )
    for path in excluded:
        if path.startswith("src/plugins/"):
            assert (ROOT / path).is_file(), f"Dead plugin path fixture: {path}"
        assert not any(fnmatchcase(path, pattern) for pattern in patterns), (
            f"{path} must not launch E2E Smoke"
        )
