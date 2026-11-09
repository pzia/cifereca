"""P0 #5069 A3: application package mutators and reconciliation share one lease."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from cifereca import updater, dev_updater
from cifereca.core.plugin_package_mutation_lock import (
    PackageMutationLeaseError, package_mutation_lease,
)
from cifereca.plugins.packages import PackageStore
from tests.test_core_p0_5069_updater_reconcile import _scenario, ID


def _other_process_trying_lock(root: Path) -> int:
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from cifereca.core.plugin_package_mutation_lock import "
        "package_mutation_lease,PackageMutationLeaseError\n"
        "try:\n"
        "  with package_mutation_lease(Path(sys.argv[1])): pass\n"
        "except PackageMutationLeaseError:\n"
        "  sys.exit(23)\n"
        "sys.exit(0)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(root)],
                            timeout=10, capture_output=True)
    return result.returncode


def test_reentrant_same_thread_and_exclusive_across_processes(tmp_path):
    root = tmp_path / "plugins" / "packages"
    with package_mutation_lease(root):
        with package_mutation_lease(root):
            assert _other_process_trying_lock(root) == 23
    assert _other_process_trying_lock(root) == 0
    lock = root.parent / ".cifereca-managed-package-mutation.lock"
    assert lock.is_file() and lock.stat().st_mode & 0o077 == 0


def test_pointer_update_under_existing_same_thread_lease(tmp_path):
    root = tmp_path / "plugins" / "packages"
    store = PackageStore(root=root)
    (store.version_root("org.test", "1.0.0")).mkdir(parents=True)
    with package_mutation_lease(root):
        store.set_active("org.test", "1.0.0")
    assert store.pointer("org.test")["active"] == "1.0.0"


def test_symlink_and_unsafe_lock_file_refused_without_writing(tmp_path):
    root = tmp_path / "plugins" / "packages"
    root.parent.mkdir(parents=True)
    outside = tmp_path / "user-private"
    outside.write_text("PRIVATE", encoding="utf-8")
    lock = root.parent / ".cifereca-managed-package-mutation.lock"
    lock.symlink_to(outside)
    with pytest.raises(PackageMutationLeaseError):
        with package_mutation_lease(root):
            pytest.fail("unsafe symlink bypassed")
    assert outside.read_text(encoding="utf-8") == "PRIVATE"
    lock.unlink()
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o666)
    with pytest.raises(PackageMutationLeaseError, match="package_mutation_lock_unsafe"):
        with package_mutation_lease(root):
            pytest.fail("unsafe mode bypassed")


def test_dev_restore_refuses_active_external_package_writer(tmp_path, monkeypatch):
    root = tmp_path / "plugins" / "packages"
    monkeypatch.setattr(dev_updater, "_plugin_store_root", lambda: root)
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from cifereca.core.plugin_package_mutation_lock import package_mutation_lease\n"
        "with package_mutation_lease(Path(sys.argv[1])):\n"
        "  print('READY', flush=True)\n"
        "  sys.stdin.readline()\n"
    )
    child = subprocess.Popen([sys.executable, "-c", script, str(root)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "READY"
        with pytest.raises(PackageMutationLeaseError, match="package_mutation_busy"):
            dev_updater._restore_plugins(["org.test"], tmp_path / "backup")
        assert not (root / "org.test").exists()
    finally:
        child.stdin.close()
        assert child.wait(timeout=10) == 0


def test_reconciliation_rejects_competing_direct_plugin_mutation(tmp_path, monkeypatch):
    root, folder, _missing = _scenario(tmp_path, monkeypatch)
    proof = updater.reconcile_lost_previous(ID)["acknowledgement_sha256"]
    packages = tmp_path / "packages"
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from cifereca.core.plugin_package_mutation_lock import package_mutation_lease\n"
        "with package_mutation_lease(Path(sys.argv[1])):\n"
        "  print('READY', flush=True)\n"
        "  sys.stdin.readline()\n"
    )
    child = subprocess.Popen([sys.executable, "-c", script, str(packages)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "READY"
        original = (folder / "transaction.json").read_bytes()
        with pytest.raises(updater.UpdateError, match="reconciliation_plugin_package_lock_unavailable"):
            updater.reconcile_lost_previous(ID, acknowledge=proof)
        assert (folder / "transaction.json").read_bytes() == original
        assert not (root / "reconciliation-audit").exists()
    finally:
        child.stdin.close()
        assert child.wait(timeout=10) == 0


def _holding_process(package_root: Path):
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from cifereca.core.plugin_package_mutation_lock import package_mutation_lease\n"
        "with package_mutation_lease(Path(sys.argv[1])):\n"
        "  print('READY',flush=True)\n"
        "  sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(package_root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "READY"
    return child


def test_core_transaction_snapshot_refuses_direct_plugin_writer(tmp_path, monkeypatch):
    package_root = tmp_path / "plugins" / "packages"
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: package_root)
    runtime = tmp_path / "runtime"
    release = runtime / "releases" / "new"
    release.mkdir(parents=True)
    child = _holding_process(package_root)
    try:
        with pytest.raises(updater.UpdateError, match="updater_package_mutation_lock_unavailable"):
            updater._create_update_transaction(runtime, release, None, None, [])
        assert not (runtime / "transactions").exists()
    finally:
        child.stdin.close()
        assert child.wait(timeout=10) == 0


def test_core_restoration_refuses_competing_plugin_writer_before_journal_change(
    tmp_path, monkeypatch,
):
    root, folder, _missing = _scenario(tmp_path, monkeypatch)
    tx = updater._read_transaction(folder)
    original = (folder / "transaction.json").read_bytes()
    child = _holding_process(tmp_path / "packages")
    try:
        with pytest.raises(updater.UpdateError, match="updater_package_mutation_lock_unavailable"):
            updater._complete_transaction_restore(root, folder, tx, "restoring")
        assert (folder / "transaction.json").read_bytes() == original
        assert updater._symlink_target(root / "previous") is None
    finally:
        child.stdin.close()
        assert child.wait(timeout=10) == 0
