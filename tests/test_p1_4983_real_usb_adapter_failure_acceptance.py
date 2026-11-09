"""#4983: actual storage.usb adapter, failure matrix on isolated mounted-folder simulation.

These tests exercise the real plugin behind the Core staging admission; they
do not assert an actual USB block mount or substitute for physical acceptance.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from cifereca.core import device_usb_file_staging as staging
from cifereca.core.device_usb_copy_supervisor import execute_usb_copy
from tests.test_p1_2854_usb_copy_supervisor import copy


@pytest.fixture
def real_usb_service(copy, monkeypatch):
    args, managed, observations, resolution = copy
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "src/plugins/storage-usb"))
    from cifereca_storage_usb import PLUGIN
    resolution.service = PLUGIN.activate(None)
    return args, managed, observations, resolution


def test_real_adapter_raises_capacity_failure_without_final_file(
    real_usb_service, monkeypatch,
):
    args, managed, _, _ = real_usb_service
    original = staging.os.write

    def volume_full(fd, payload):
        raise OSError(errno.ENOSPC, "simulated full USB filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(staging.os, "write", volume_full)
        outcome = execute_usb_copy(**args)
    assert outcome.status == "recovery_required"
    assert outcome.reason == "usb_stage_io_failed"
    assert args["journal"].get(args["consent"].operation_id).phase == "pending"
    final = managed / (args["consent"].sha256 + ".epub")
    assert not final.exists()
    stages = tuple(managed.glob(".cifereca-usb-*.part"))
    assert len(stages) == 1
    assert stages[0].stat().st_size == 0
    assert not outcome.receipt


def test_inactive_storage_plugin_keeps_target_absent_and_no_journal(
    real_usb_service,
):
    args, managed, _, resolved = real_usb_service
    resolved.capabilities = ()
    outcome = execute_usb_copy(**args)
    assert outcome.status == "blocked"
    assert outcome.reason == "usb_stage_transport_unavailable"
    assert args["journal"].get(args["consent"].operation_id) is None
    assert list(managed.iterdir()) == []


def test_real_adapter_does_not_adopt_preexisting_equal_hash_file(
    real_usb_service,
):
    args, managed, _, _ = real_usb_service
    target = managed / (args["consent"].sha256 + ".epub")
    target.write_bytes(args["payload"])
    outcome = execute_usb_copy(**args)
    assert outcome.status == "blocked"
    assert outcome.reason == "usb_copy_target_not_clear"
    assert args["journal"].get(args["consent"].operation_id) is None
    assert list(managed.iterdir()) == [target]


def test_real_adapter_rejects_replayed_completed_copy_and_reobserves(
    real_usb_service,
):
    args, managed, _, resolved = real_usb_service
    first = execute_usb_copy(**args)
    assert first.status == "completed"
    target = managed / (args["consent"].sha256 + ".epub")
    observed = resolved.service.observe_usb_files(
        managed.parent, ("Books/" + target.name,),
    )
    assert len(observed) == 1
    assert observed[0].status == "present"
    assert observed[0].sha256 == args["consent"].sha256
    assert observed[0].size == len(args["payload"])
    replay = execute_usb_copy(**args)
    assert replay.status == "blocked"
    assert replay.reason == "usb_copy_consent_already_used"
    assert list(managed.iterdir()) == [target]
