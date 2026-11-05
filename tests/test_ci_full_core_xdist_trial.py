"""CI #4556: full Core xdist trial never weakens scoped or required gates."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-ci.yml"
MAIN_WORKFLOW = ROOT / ".github/workflows/core-tests.yml"


def test_two_workers_only_in_exhaustive_core_mode():
    content = WORKFLOW.read_text(encoding="utf-8")
    assert content.count('if [[ "$CORE_TEST_MODE" == "full" ]]; then') == 1
    runner = content.split("- name: Run Core tests", 1)[1]
    runner = runner.split("\n      - name:", 1)[0]
    assert 'python -m pytest -q --durations=10 -n 2 --dist=loadfile $CORE_TEST_TARGETS' in runner
    assert 'python -m pytest -q --durations=10 $CORE_TEST_TARGETS' in runner
    assert 'if [[ "$CORE_TEST_MODE" == "full" ]]' in runner
    assert 'else\n            python -m pytest -q --durations=10 $CORE_TEST_TARGETS' in runner


def test_xdist_installed_only_for_exhaustive_core():
    content = WORKFLOW.read_text(encoding="utf-8")
    installer = content.split("- name: Install Core test dependencies", 1)[1]
    installer = installer.split("\n      - name:", 1)[0]
    assert 'if [[ "${{ steps.scope.outputs.core_mode }}" == "full" ]]; then' in installer
    assert 'python -m pip install --disable-pip-version-check "pytest-xdist>=3.6,<4"' in installer
    assert content.count('pytest-xdist>=3.6,<4') == 1


def test_required_context_and_other_gates_still_present():
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "    name: PR CI\n" in content
    assert "  pull_request:\n" in content
    assert "  workflow_dispatch:\n" in content
    for name in (
        "BnF HTTPX reader acceptance",
        "Paris/BnF lending conformance",
        "Run plugin tests",
        "Validate plugin packaging",
        "Smoke installed Core wheel",
    ):
        assert name in content


def test_main_exhaustive_uses_two_workers_but_selected_remains_sequential():
    content = MAIN_WORKFLOW.read_text(encoding="utf-8")
    assert "  workflow_dispatch:\n" in content
    assert "    branches: [main]\n" in content
    assert "python3 scripts/ci_select_core_push.py" in content
    assert 'CORE_TEST_TARGETS: ${{ steps.scope.outputs.core_targets }}' in content
    assert 'if [[ "$CORE_TEST_MODE" == "full" ]]; then' in content
    assert 'python -m pytest -q --durations=10 -n 2 --dist=loadfile "${selected[@]}"' in content
    assert 'else\n            python -m pytest -q --durations=10 "${selected[@]}"' in content
    assert content.count("if: steps.scope.outputs.core_mode != 'skip'") == 3
    assert 'if [[ "${{ steps.scope.outputs.core_mode }}" == "full" ]]; then' in content
    assert content.count('python -m pip install --disable-pip-version-check "pytest-xdist>=3.6,<4"') == 1
    assert 'Empty test selection: refusing to skip the Core suite' in content
