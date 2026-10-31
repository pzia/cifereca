"""Synthetic Git acceptance of the fail-closed Core push selector (#3914)."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from scripts.ci_select_core_push import select_push

ROOT = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True,
    ).strip()


def write(root: Path, path: str, content: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")


def commit(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


def fixture(tmp_path: Path) -> tuple[Path, str]:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "CI")
    git(tmp_path, "config", "user.email", "ci@example.invalid")
    script = tmp_path / "scripts/ci-select-core-tests.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "scripts/ci-select-core-tests.py", script)
    shutil.copyfile(
        ROOT / "scripts/ci_test_dependency_guard.py",
        script.parent / "ci_test_dependency_guard.py",
    )
    write(tmp_path, "src/cifereca/core/storage.py", "VALUE = 1\n")
    write(tmp_path, "src/cifereca/gui/cover_preview.py", "VALUE = 1\n")
    for path in (
        "tests/test_storage.py",
        "tests/test_core0931_cover_preview.py",
        "tests/test_gui_compat.py",
        "tests/test_regressions_099.py",
        "tests/test_e2e_instrumentation.py",
        "tests/test_e2e_runner_contract.py",
        "tests/test_core_ui_interactive_startup.py",
    ):
        write(tmp_path, path, "def test_placeholder(): pass\n")
    return tmp_path, commit(tmp_path, "initial")


def select(root: Path, before: str, head: str, event: str = "push"):
    return select_push(root, event=event, before=before, head=head)


def test_changed_test_only_runs_that_test_without_full_suite(tmp_path):
    root, before = fixture(tmp_path)
    write(root, "tests/test_added.py", "def test_new(): assert True\n")
    head = commit(root, "one test")
    assert select(root, before, head) == (
        "changed", "tests/test_added.py", "verified-linear-push",
    )



def test_changed_test_module_reused_as_fixture_forces_full_suite(tmp_path):
    root, _initial = fixture(tmp_path)
    write(root, "tests/test_dependent.py", "from tests.test_storage import StorageTests\n")
    before = commit(root, "consumer established")
    write(root, "tests/test_storage.py", "def test_changed_fixture(): pass\n")
    head = commit(root, "modify shared test fixture")
    assert select(root, before, head) == ("full", "tests", "selector-requires-full")


def test_dynamic_test_consumer_forces_full_on_main_push(tmp_path):
    root, _initial = fixture(tmp_path)
    write(root, "tests/test_dependent.py", 'pytest_plugins = ["tests.test_storage"]\n')
    before = commit(root, "plugin consumer established")
    write(root, "tests/test_storage.py", "def test_changed_fixture(): pass\n")
    head = commit(root, "modify shared pytest plugin")
    assert select(root, before, head) == ("full", "tests", "selector-requires-full")


def test_main_selector_scans_consumers_only_in_canonical_child(tmp_path):
    # A duplicate scan here reads the entire test tree a second time.
    push_script = (ROOT / "scripts/ci_select_core_push.py").read_text(encoding="utf-8")
    canonical = (ROOT / "scripts/ci-select-core-tests.py").read_text(encoding="utf-8")
    assert "has_test_consumers" not in push_script
    assert "has_test_consumers(root, changed_tests)" in canonical

    root, _initial = fixture(tmp_path)
    write(
        root, "tests/test_consumer.py",
        '__import__("tests." + module_name)\\n',
    )
    before = commit(root, "unresolved test namespace consumer")
    write(root, "tests/test_storage.py", "def test_changed(): pass\n")
    head = commit(root, "modify imported test")
    assert select(root, before, head) == ("full", "tests", "selector-requires-full")


def test_ui_component_uses_existing_core_selection_and_smoke_targets(tmp_path):
    root, before = fixture(tmp_path)
    write(root, "src/cifereca/gui/cover_preview.py", "VALUE = 2\n")
    head = commit(root, "component")
    mode, targets, reason = select(root, before, head)
    assert mode == "ui-component" and reason == "verified-linear-push"
    assert "tests/test_core0931_cover_preview.py" in targets.split()
    assert "tests/test_gui_compat.py" in targets.split()
    assert "tests/test_storage.py" not in targets.split()


def test_shared_core_or_ambiguous_changes_preserve_full_suite(tmp_path):
    root, before = fixture(tmp_path)
    write(root, "src/cifereca/core/storage.py", "VALUE = 2\n")
    head = commit(root, "shared Core")
    assert select(root, before, head)[0:2] == ("full", "tests")

    # A changed test cannot hide an unrelated changed script.
    before = head
    write(root, "tests/test_storage.py", "def test_storage(): pass\n")
    write(root, "scripts/unrelated.py", "print('changed')\n")
    head = commit(root, "ambiguous dependency")
    assert select(root, before, head) == ("full", "tests", "shared-or-ambiguous-path")


def test_deletions_renames_and_missing_history_do_not_skip_tests(tmp_path):
    root, before = fixture(tmp_path)
    (root / "tests/test_storage.py").unlink()
    head = commit(root, "delete test")
    assert select(root, before, head)[0:2] == ("full", "tests")

    before = head
    git(root, "mv", "tests/test_gui_compat.py", "tests/test_gui_compat_renamed.py")
    head = commit(root, "rename")
    assert select(root, before, head)[0:2] == ("full", "tests")

    assert select(root, "0" * 40, head)[0:2] == ("full", "tests")
    assert select(root, "f" * 40, head)[0:2] == ("full", "tests")
    assert select(root, head, head)[0:2] == ("full", "tests")


def test_manual_and_selector_failures_revert_to_full_suite(tmp_path):
    root, before = fixture(tmp_path)
    write(root, "tests/test_added.py", "def test_new(): assert True\n")
    head = commit(root, "new test")
    assert select(root, before, head, "workflow_dispatch") == (
        "full", "tests", "manual-or-non-push",
    )
    missing = root / "not-a-script.py"
    assert select_push(
        root, event="push", before=before, head=head, selector=missing,
    )[0:2] == ("full", "tests")


def test_workflow_runs_selected_targets_and_supports_manual_full():
    source = (ROOT / ".github/workflows/core-tests.yml").read_text(encoding="utf-8")
    assert "  workflow_dispatch:\n" in source
    assert "fetch-depth: 0" in source
    assert "python3 scripts/ci_select_core_push.py" in source
    assert '--event "$GITHUB_EVENT_NAME"' in source
    assert '--github-output "$GITHUB_OUTPUT"' in source
    assert "CORE_TEST_TARGETS: ${{ steps.scope.outputs.core_targets }}" in source
    assert 'python -m pytest -q --durations=10 "${selected[@]}"' in source
