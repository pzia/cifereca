#!/usr/bin/env python3
"""Fail-closed E2E GTK scenario selection (issue #1236)."""
from __future__ import annotations
import argparse
from pathlib import Path

LLM = ("llm-settings-1366", "llm-settings-1600")
MAINT = ("maintenance-tools-920", "maintenance-tools-1024", "maintenance-tools-1280", "maintenance-tools-1366", "maintenance-tools-1600")
# P0 source operations affect the real Synchronisation page even when no
# GTK module changes. Baselines cover source rows, exact account targeting,
# preview/audit gating and independent inventory/acquisition feedback.
SYNC_CORE = ("mainwindow-baseline-1366", "mainwindow-baseline-1600")
SYNC_PRODUCERS = frozenset((
    "src/cifereca/brokers.py",
    "src/cifereca/core/source_materialization.py",
    "src/cifereca/core/numilog_materialization_startup.py",
    "src/cifereca/core/numilog_materialization_checkpoint.py",
    "src/cifereca/core/numilog_direct_acquisition.py",
    "src/cifereca/core/loan_local_preparation.py",
    "src/cifereca/core/loan_provider_delivery.py",
    "src/cifereca/core/sync_coordination.py",
))
BOOK_DETAIL = ("book-detail-1300", "book-detail-1366", "book-detail-1600")
HOME = ("mainwindow-baseline-1366", "mainwindow-baseline-1600")
CORE = ("search-filter-p0",) + BOOK_DETAIL + ("mainwindow-baseline-1366",
        "mainwindow-baseline-1600", "startup", "core-navigation") + LLM + MAINT + ("public-fault-search-1366", "kobo-fault-search-1366")
DIAG = ("diagnostics-1366", "diagnostics-large-1600", "diagnostics-states-1366",
        "diagnostics-structured-1366", "diagnostics-volume-1366",
        "diagnostics-navigation-1366", "diagnostics-navigation-slow-1366")
# CI smoke for P1 bibliographic detail plugins (#1599). These existing GTK
# scenarios are regression guards, not plugin-specific acceptance evidence.
DETAIL = BOOK_DETAIL + ("mainwindow-baseline-1366",
          "mainwindow-baseline-1600")
CONSTELLATIONS_GTK = ("constellations-detail-1366", "constellations-detail-1600")
CONSTELLATIONS = DETAIL + CONSTELLATIONS_GTK
RECOMMENDATIONS_GTK = ("recommendations-detail-1366", "recommendations-detail-1600")
RECOMMENDATIONS = DETAIL + RECOMMENDATIONS_GTK
ALL = CORE + DIAG + CONSTELLATIONS_GTK + RECOMMENDATIONS_GTK
SHELL = ("mainwindow-baseline-1366", "mainwindow-baseline-1600",
         "startup", "core-navigation", "diagnostics-navigation-1366",
         "diagnostics-navigation-slow-1366")
# MainWindow changes affect core/detail and Diagnostics navigation probes.
MAINWINDOW = tuple(dict.fromkeys(
    CORE + ("diagnostics-navigation-1366", "diagnostics-navigation-slow-1366")
))
# The Diagnostics common probe is imported by every other Diagnostics probe;
# navigation probes also implement both MainWindow baseline scenarios.
DIAG_SHARED = DIAG + ("mainwindow-baseline-1366", "mainwindow-baseline-1600")
DIAG_STATES = ("diagnostics-states-1366", "diagnostics-navigation-1366",
               "diagnostics-navigation-slow-1366",
               "mainwindow-baseline-1366", "mainwindow-baseline-1600")

LOCAL_GUI = {
    "action_layout.py": BOOK_DETAIL,
    "action_widgets.py": BOOK_DETAIL,
    "book_facets.py": ("search-filter-p0",) + BOOK_DETAIL,
    "book_widgets.py": BOOK_DETAIL + ("search-filter-p0",),
    "cover_loader.py": BOOK_DETAIL,
    "cover_preview.py": BOOK_DETAIL,
    "facet_text.py": ("search-filter-p0",),
    "query_controls.py": ("search-filter-p0", "core-navigation"),
    "query_model.py": ("search-filter-p0",) + BOOK_DETAIL,
    "search_intent.py": ("search-filter-p0",),
    "settings_ai_actions.py": LLM,
    "settings_ai_section.py": LLM,
    "settings_lifetime.py": LLM,
    "llm_settings.py": LLM,
    "maintenance_tools.py": MAINT,
    "maintenance_result_view.py": MAINT,
    "maintenance_line_progress.py": MAINT,
    "maintenance_line_widgets.py": MAINT,
}
PROBES = {
    "e2e/public_fault_search_probe.py": ("public-fault-search-1366", "kobo-fault-search-1366"),
    "e2e/book_detail_probe.py": BOOK_DETAIL,
    "e2e/constellations_detail_probe.py": CONSTELLATIONS_GTK,
    "e2e/recommendations_detail_probe.py": RECOMMENDATIONS_GTK,
    "e2e/llm_settings_probe.py": LLM,
    "e2e/maintenance_tools_probe.py": MAINT,
    "e2e/search_filter_probe.py": ("search-filter-p0",),
    "e2e/diagnostics_page_probe.py": DIAG_SHARED,
    "e2e/diagnostics_states_probe.py": DIAG_STATES,
    "e2e/diagnostics_structured_probe.py": ("diagnostics-structured-1366",),
    "e2e/diagnostics_volume_probe.py": ("diagnostics-volume-1366",),
    "e2e/diagnostics_navigation_probe.py": (
        "diagnostics-navigation-1366", "diagnostics-navigation-slow-1366",
        "mainwindow-baseline-1366", "mainwindow-baseline-1600"),
}
GLOBAL = {
    "e2e/run.py", "pyproject.toml",
    "scripts/ci-select-e2e.py", "scripts/ci-select-core-tests.py",
    "scripts/ci-install-gtk-e2e-runtime.sh",
    "environnements/openai-chat-debian/capture_app.py",
    ".github/workflows/e2e-smoke.yml",
    "tests/test_e2e_runner_contract.py",
    "tests/test_e2e_ci_paths.py", "tests/test_ci_select_e2e.py",
}

def select(paths: list[str], *, full: bool = False) -> tuple[str, ...]:
    if full:
        return ALL
    changed = [p.strip() for p in paths if p.strip()]
    if not changed:
        return ALL  # empty diff is not a silent pass
    selected: set[str] = set()
    for path in changed:
        # Explicit standalone synthetic load tool: no application/GTK imports,
        # provider calls or runtime configuration. Its tests run in Core CI.
        if path == "scripts/benchmark_task_admission.py":
            continue
        # Only explicit release packaging reads this version snapshot. A
        # routine plugin bump must not expand GTK validation to unrelated pages.
        if path.endswith((".md", ".rst", ".txt")) or path == "src/cifereca/plugin-release.json":
            continue
        if path in GLOBAL or path.startswith("e2e/fixtures/"):
            return ALL
        if path in PROBES:
            selected.update(PROBES[path])
        elif path.startswith("e2e/"):
            return ALL  # unknown or shared E2E helper
        elif path.startswith("src/plugins/diagnostics-page/") or path == "src/cifereca/core/diagnostic_numilog_projection.py":
            selected.update(DIAG)
        elif path.startswith("src/plugins/home-page/"):
            selected.update(HOME)
        elif path.startswith("src/plugins/constellations/"):
            selected.update(CONSTELLATIONS)
        elif path.startswith("src/plugins/recommendations/"):
            selected.update(RECOMMENDATIONS)
        elif path in SYNC_PRODUCERS:
            selected.update(SYNC_CORE)
        elif path.startswith("src/cifereca/builtin_ui/"):
            selected.update(SHELL)
        elif path == "src/cifereca/gui/main_window.py":
            selected.update(MAINWINDOW)
        elif path in ("src/cifereca/app.py", "src/cifereca/gui/shell_model.py",
                      "src/cifereca/gui/navigation_history.py"):
            selected.update(SHELL)
        elif path.startswith("src/cifereca/gui/"):
            component = path.removeprefix("src/cifereca/gui/")
            if component not in LOCAL_GUI:
                return ALL
            selected.update(LOCAL_GUI[component])
        elif path.startswith("tests/") and path.endswith(".py"):
            continue  # unrelated test alongside a known UI change
        else:
            return ALL  # unknown dependency in an otherwise targeted PR
    return tuple(name for name in ALL if name in selected)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-files", type=Path)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    if not args.full and args.changed_files is None:
        parser.error("--changed-files is required unless --full")
    paths = args.changed_files.read_text(encoding="utf-8").splitlines() if args.changed_files else []
    scenarios = select(paths, full=args.full)
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write("scenarios=" + " ".join(scenarios) + "\n")
        output.write(f"count={len(scenarios)}\n")
        output.write(f"total={len(ALL)}\n")
        output.write(f"mode={'full' if scenarios == ALL else 'selected' if scenarios else 'docs-only'}\n")
    print(f"GTK E2E selection: {len(scenarios)}/{len(ALL)}: {', '.join(scenarios) or 'docs only'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
