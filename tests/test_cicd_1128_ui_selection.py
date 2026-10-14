"""CI #1128: route headless Search regression tests on every relevant UI change."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _selector():
    spec = importlib.util.spec_from_file_location(
        "ci_select_core_tests_1128",
        ROOT / "scripts/ci-select-core-tests.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_search_ui_source_selects_both_coreui_filename_conventions(tmp_path):
    """A later UI PR must still exercise regressions introduced by #1128."""
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in (
        "test_coreui_1128_search_host_port.py",
        "test_coreui_1128_search_detail_context_regressions.py",
        "test_core_ui_search_holdings_generation.py",
    ):
        (tests / name).touch()
    mode, selected = _selector().select(
        ["src/cifereca/gui/search_host_port.py"], root=tmp_path,
    )
    assert mode == "ui"
    assert selected == [
        "tests/test_core_ui_search_holdings_generation.py",
        "tests/test_coreui_1128_search_detail_context_regressions.py",
        "tests/test_coreui_1128_search_host_port.py",
    ]


def test_unrelated_test_names_are_not_widened_by_new_glob(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_coreui_1128_search_host_port.py").touch()
    (tests / "test_core_functional_foo.py").touch()
    mode, selected = _selector().select(
        ["src/cifereca/gui/search_controller.py"], root=tmp_path,
    )
    assert mode == "ui"
    assert selected == ["tests/test_coreui_1128_search_host_port.py"]
