"""P0 #3196: reproducible LOC budget for provider-specific installed Core."""
from __future__ import annotations

from pathlib import Path

from scripts.ci_kobo_core_line_inventory import _category, inventory


ROOT = Path(__file__).resolve().parents[1]
# Contract-first baseline, 2026-11-08 after green #5032 and P0 W4 dead-helper extraction. Only decrease this
# ceiling after cuts merge; never reclassify production code to hide growth.
BASELINE = 7218


def test_kobo_dedicated_core_surface_never_grows_from_baseline():
    result = inventory(ROOT)
    assert result["metric"] == "core.kobo.dedicated-python-lines/1"
    assert result["dedicated_lines"] <= BASELINE
    assert result["dedicated_lines"] == sum(result["groups"].values())
    assert result["dedicated_files"] == len(result["per_file"])
    assert result["groups"]["core"] > 0
    assert result["groups"]["kobo_subpackage"] > 0
    assert result["groups"]["gui_legacy"] > 0
    assert result["nonblank_noncomment_lines"] <= result["dedicated_lines"]
    assert result["outside_dedicated_kobo_literal_lines"] >= 0


def test_scope_is_explicit_and_does_not_count_provider_plugin_code():
    assert _category(Path("src/cifereca/core/kobo_backup.py")) == "core"
    assert _category(Path("src/cifereca/core/canonical_kobo_selected.py")) == "core"
    assert _category(Path("src/cifereca/kobo/executor.py")) == "kobo_subpackage"
    assert _category(Path("src/cifereca/gui/settings_legacy_kobo.py")) == "gui_legacy"
    assert _category(Path("src/cifereca/core/source_reference_guard.py")) is None
    assert _category(Path("src/cifereca/services.py")) is None
    assert _category(Path("src/plugins/provider-kobo/kobo.py")) is None


def test_inventory_counts_generic_literals_separately(tmp_path):
    core = tmp_path / "src" / "cifereca"
    (core / "core").mkdir(parents=True)
    (core / "kobo").mkdir()
    (core / "gui").mkdir()
    (core / "core" / "kobo_backup.py").write_text(
        "# legacy\n\nprint('Kobo')\n", encoding="utf-8",
    )
    (core / "kobo" / "vault.py").write_text(
        "pass\n", encoding="utf-8",
    )
    (core / "gui" / "settings_legacy_kobo.py").write_text(
        "# comment\n", encoding="utf-8",
    )
    (core / "services.py").write_text(
        "  # kobo\nservice.kobo_backup()\n", encoding="utf-8",
    )
    result = inventory(tmp_path)
    assert result["groups"] == {
        "core": 3, "kobo_subpackage": 1, "gui_legacy": 1,
    }
    assert result["dedicated_lines"] == 5
    assert result["nonblank_noncomment_lines"] == 2
    assert result["outside_dedicated_kobo_literal_lines"] == 2
    assert result["outside_dedicated_files_with_kobo_literal"] == 1
