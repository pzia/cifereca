"""Guard path-scoped GTK E2E against silent Milan/Jeunesse regressions.

The E2E workflow must run on pull requests changing the real youth source
adapters, their HTML fixtures or their GTK runtime setup; unrelated plugins
and release-only work must not trigger this heavyweight recipe.
"""
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


def test_milan_and_fixture_changes_trigger_real_gtk_e2e():
    patterns = e2e_pull_request_patterns()
    included = (
        "src/plugins/youth-catalog/youth_page.py",
        "src/cifereca/brokers.py",
        "src/cifereca/core/source_materialization.py",
        "src/cifereca/core/loan_local_preparation.py",
        "src/cifereca/core/loan_provider_delivery.py",
        "src/cifereca/core/numilog_direct_acquisition.py",
        "src/cifereca/core/sync_coordination.py",
        "src/plugins/youth-detail-enrichment/enrich.py",
        "src/plugins/constellations/page.py",
        "src/plugins/home-page/cifereca_home.py",
        "src/plugins/home-page/cifereca-plugin.json",
        "src/plugins/recommendations/page.py",
        "src/plugins/youth-source-milan/milan_youth.py",
        "src/plugins/youth-source-milan/cifereca-plugin.json",
        "simulation/youth_sources/pseudo_youth_sources_service.py",
        "simulation/milan/pseudo_milan_service.py",
        "scripts/ci-install-gtk-e2e-runtime.sh",
        "e2e/youth_page_probe.py",
        "tests/test_e2e_runner_contract.py",
    )
    for path in included:
        assert any(fnmatchcase(path, pattern) for pattern in patterns), (
            f"{path} must trigger E2E Smoke"
        )


def test_unrelated_work_stays_out_of_heavy_gtk_gate():
    patterns = e2e_pull_request_patterns()
    excluded = (
        "src/plugins/kobo-vault/kobo_vault.py",
        "src/plugins/instapaper/instapaper.py",
        "docs/PLUGINS-ONLY-RELEASE.md",
        ".github/workflows/plugins-only-release.yml",
        "README.md",
    )
    for path in excluded:
        assert not any(fnmatchcase(path, pattern) for pattern in patterns), (
            f"{path} must not launch E2E Smoke"
        )
