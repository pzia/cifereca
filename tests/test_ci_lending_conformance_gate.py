"""P1 #3122: provider changes trigger their shared Core/Plugin conformance gate."""
from pathlib import Path

import pytest

from scripts import ci_lending_conformance_gate as gate


@pytest.fixture
def root(tmp_path):
    for file in gate.TESTS:
        path = tmp_path / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic test placeholder\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("path", [
    "src/plugins/provider-paris/cifereca-plugin.json",
    "src/plugins/provider-paris/cifereca_paris.py",
    "src/plugins/provider-paris/tests/test_snapshot.py",
    "src/plugins/provider-bnf-pret/cifereca_bnf_pret.py",
    "src/plugins/provider-bnf-pret/tests/test_http_provider.py",
    "scripts/ci_lending_conformance_gate.py",
    "tests/test_core_p1_3122_real_manifest_lending_conformance.py",
    ".github/workflows/pr-ci.yml",
])
def test_provider_or_contract_change_selects_conformance(root, path):
    assert gate.selected([path], root)


@pytest.mark.parametrize("path", [
    "src/plugins/provider-kobo/cifereca_kobo.py",
    "src/cifereca/core/book_runtime.py",
    "src/plugins/baen/cifereca_baen.py",
    "README.md",
])
def test_unrelated_changes_do_not_select_conformance(root, path):
    assert not gate.selected([path], root)


def test_manual_full_and_missing_test_fail_closed(root):
    assert gate.selected([], root, full=True)
    (root / gate.TESTS[1]).unlink()
    with pytest.raises(ValueError, match="^lending_conformance_suite_missing$"):
        gate.selected(["src/plugins/provider-paris/test.py"], root)
    with pytest.raises(ValueError, match="^lending_conformance_suite_missing$"):
        gate.selected([], root, full=True)
    assert not gate.selected(["README.md"], root)


def test_workflow_wires_synthetic_suite_and_core_dependencies():
    source = (Path(__file__).resolve().parents[1] / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
    assert "lending_conformance=$(python3 scripts/ci_lending_conformance_gate.py select" in source
    assert "lending_conformance=$lending_conformance" in source
    assert "steps.scope.outputs.lending_conformance" in source
    assert 'python scripts/ci_lending_conformance_gate.py run' in source
    assert 'steps.scope.outputs.bnf_reader' in source
    assert 'python -m pip install --disable-pip-version-check -e .' in source
    step = source.split("- name: Paris/BnF lending conformance", 1)[1].split("\n      - name:", 1)[0]
    assert "PYTHONPATH: ${{ github.workspace }}/src" in step
    assert "playwright" not in step and "chromium" not in step
    assert "install-deps" not in step
