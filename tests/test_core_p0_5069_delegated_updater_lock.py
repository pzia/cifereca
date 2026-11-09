"""Integration coverage of the P0 #5069 cross-process updater lease.

The child must inherit an actual locked kernel file description. Merely
supplying an environment variable never authorizes a bypass.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cifereca import updater


CHILD = (
    "import pathlib,sys\n"
    "from cifereca import updater\n"
    "with updater._exclusive_updater_lock(pathlib.Path(sys.argv[1])):\n"
    "    assert updater._UPDATER_LOCK_DEPTH.borrowed is True\n"
    "    print('borrowed-lease-valid')\n"
)


def _env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CIFERECA_INSTALL_ROOT"] = str(root)
    # The nested Python interpreter must resolve the exact checkout under test.
    src = str(Path(updater.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env.pop(updater._UPDATER_INHERITED_LOCK_FD_ENV, None)
    return env


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX pass_fds and flock")
def test_core_delegation_allows_child_without_unlocking_competing_process(tmp_path):
    root = tmp_path / "runtime"
    with updater._exclusive_updater_lock(root):
        fd = updater._UPDATER_LOCK_DEPTH.fd
        env = _env(root)
        env[updater._UPDATER_INHERITED_LOCK_FD_ENV] = str(fd)
        child = subprocess.run(
            [sys.executable, "-c", CHILD, str(root)],
            env=env, pass_fds=(fd,), capture_output=True, text=True, timeout=12,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "borrowed-lease-valid"
        # Unrelated processes opening a different file description still fail.
        competitor = subprocess.run(
            [sys.executable, "-c",
             "import fcntl,os,sys;f=os.open(sys.argv[1],os.O_RDWR);"
             "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)",
             str(root / ".cifereca-updater.lock")],
            env=_env(root), capture_output=True, timeout=12,
        )
        assert competitor.returncode != 0
    assert updater._UPDATER_LOCK_DEPTH.fd is None


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX pass_fds and flock")
def test_unrelated_child_cannot_claim_lease_by_environment_string(tmp_path):
    root = tmp_path / "runtime"
    with updater._exclusive_updater_lock(root):
        env = _env(root)
        # Invalid or closed descriptors cannot impersonate the parent.
        env[updater._UPDATER_INHERITED_LOCK_FD_ENV] = "998"
        child = subprocess.run(
            [sys.executable, "-c", CHILD, str(root)],
            env=env, capture_output=True, text=True, timeout=12,
        )
        assert child.returncode != 0
        assert "borrowed-lease-valid" not in child.stdout


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX pass_fds and flock")
def test_wrong_inherited_inode_rejected(tmp_path):
    root = tmp_path / "runtime"
    impostor = tmp_path / "other.lock"
    impostor.write_text("")
    impostor.chmod(0o600)
    with updater._exclusive_updater_lock(root):
        fd = os.open(impostor, os.O_RDWR)
        try:
            env = _env(root)
            env[updater._UPDATER_INHERITED_LOCK_FD_ENV] = str(fd)
            child = subprocess.run(
                [sys.executable, "-c", CHILD, str(root)],
                env=env, pass_fds=(fd,), capture_output=True, text=True, timeout=12,
            )
            assert child.returncode != 0
            assert "borrowed-lease-valid" not in child.stdout
        finally:
            os.close(fd)
