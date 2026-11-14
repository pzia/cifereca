"""P1 #6077: automatic exact-UUID USB preview without repeated folder chooser.

All observed kernel mounts and Core attestation are synthetic.  No mounted
volume, network, provider, USB write, copy or delete is exercised.
"""
from __future__ import annotations

from pathlib import Path
from time import time
from types import SimpleNamespace as NS

import pytest

from cifereca.core import device_usb_discovery as discovery
from cifereca.core import device_usb_dry_run as worker
from cifereca.core import device_usb_mount as linux_mount
from cifereca.gui.reader_usb_preview_actions import (
    _discover_mounted_binding, request_reader_usb_preview,
)
from tests.test_p1_2854_reader_usb_preview_actions import (
    FAKE_GTK, FakeNative, report, rig, row,
)


UUID = "uuid-alice-0001"
MOUNT = Path("/mnt/synthetic/reader-alice")


def volume(*, uuid=UUID, mount=MOUNT, mount_id=189):
    return discovery.MountedUsbVolume(
        usb_fingerprint=uuid, mountpoint=mount, filesystem="exfat",
        label="SanDisk", capacity_bytes=500_000_000_000,
        major=8, minor=17, mount_id=mount_id,
    )


def proof():
    return linux_mount.LinuxUsbMount(
        identity_id="alice", device_id="reader-alice",
        usb_fingerprint=UUID, mountpoint=MOUNT, filesystem="exfat",
        major=8, minor=17, checked_at=int(time()), mount_id=189,
    )


def fake_kernel(monkeypatch, supplied):
    reads = []
    attestations = []

    def discover():
        reads.append("read")
        return tuple(supplied())

    def observe(**kwargs):
        attestations.append(kwargs)
        return proof()

    monkeypatch.setattr(discovery, "discover_linux_usb_volumes", discover)
    monkeypatch.setattr(linux_mount, "observe_linux_usb_mount", observe)
    return reads, attestations


def test_default_action_discovers_off_gtk_then_scans_exact_binding_without_chooser(
    tmp_path, monkeypatch,
):
    db, window, root = rig(tmp_path)
    seen, proofs = fake_kernel(monkeypatch, lambda: (volume(),))
    jobs = []
    monkeypatch.setattr(
        worker, "inspect_usb_dry_run",
        lambda **kwargs: (jobs.append(kwargs) or report(rows=(row("skip"),))),
    )
    before_choosers = len(FakeNative.created)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    # Click itself queues only; no filesystem, no chooser, no Core scan on GTK.
    assert seen == [] and proofs == [] and jobs == []
    assert len(FakeNative.created) == before_choosers
    assert len(window.runner.calls) == 1
    label, discover, found, error, options = window.runner.calls[0]
    assert label.startswith("Rechercher")
    assert options["origin"] == "user"
    assert options["kind"] == "maintenance-inspection"
    selected = discover()
    assert selected == volume()
    assert len(seen) == len(proofs) == 1
    assert proofs[0]["identity_id"] == "alice"
    assert proofs[0]["mountpoint"] == MOUNT
    found(selected)
    assert len(window.runner.calls) == 2
    assert len(FakeNative.created) == before_choosers
    label, inspect, done, fail, options = window.runner.calls[1]
    assert label.startswith("Examiner")
    assert jobs == []  # Not even a dry-run before worker execution.
    result = inspect()
    assert len(seen) == len(proofs) == 2  # Mandatory fresh reattestation.
    assert jobs[0]["mountpoint"] == MOUNT
    assert jobs[0]["identity_root"] == root
    assert jobs[0]["storage"] is db
    done(result)
    assert "Aperçu USB ponctuel" in window._reader_status_label.text
    assert "Aucun transfert" in window._reader_status_label.text
    assert len(FakeNative.created) == before_choosers


@pytest.mark.parametrize("scenario", [
    "absent", "wrong_uuid", "duplicate_uuid", "invalid_observation",
    "attestation_refused",
])
def test_not_unique_or_unattested_mount_offers_only_explicit_manual_fallback(
    tmp_path, monkeypatch, scenario,
):
    db, window, root = rig(tmp_path)
    volumes = {
        "absent": (),
        "wrong_uuid": (volume(uuid="another-uuid-0001"),),
        "duplicate_uuid": (volume(), volume(mount=Path("/mnt/other"))),
        "invalid_observation": (object(),),
        "attestation_refused": (volume(),),
    }[scenario]
    seen, proofs = fake_kernel(monkeypatch, lambda: volumes)
    if scenario == "attestation_refused":
        monkeypatch.setattr(
            linux_mount, "observe_linux_usb_mount", lambda **_: None,
        )
    before = len(FakeNative.created)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    assert len(window.runner.calls) == 1
    assert len(FakeNative.created) == before
    _label, discover, done, err, _opts = window.runner.calls[0]
    assert discover() is None
    assert len(seen) == 1
    assert len(proofs) == (0 if scenario != "attestation_refused" else 0)
    done(None)
    chooser = window._reader_usb_preview_chooser
    assert chooser.shown and len(FakeNative.created) == before + 1
    assert "Aucun transfert" in window._reader_status_label.text
    assert len(window.runner.calls) == 1
    chooser.respond(None, response="cancel")
    assert len(window.runner.calls) == 1


def test_remount_between_selection_and_worker_is_refused_before_core_scan(
    tmp_path, monkeypatch,
):
    db, window, root = rig(tmp_path)
    shown = [volume()]
    seen, proofs = fake_kernel(monkeypatch, lambda: tuple(shown))
    scanned = []
    monkeypatch.setattr(
        worker, "inspect_usb_dry_run",
        lambda **kwargs: scanned.append(kwargs),
    )
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    _, discover, done, _err, _opts = window.runner.calls[0]
    original = discover()
    assert original == volume()
    done(original)
    assert len(window.runner.calls) == 2
    shown[:] = [volume(mount_id=190)]
    _label, inspect, ok, failed, _options = window.runner.calls[1]
    with pytest.raises(ValueError, match="USB mount changed"):
        inspect()
    assert scanned == []
    failed(ValueError("redacted"))
    assert "Aperçu USB indisponible" in window._reader_status_label.text


@pytest.mark.parametrize("revocation", [
    "identity", "account", "generation", "device", "route", "binding",
])
def test_profile_or_binding_change_revokes_discovery_without_chooser_or_scan(
    tmp_path, monkeypatch, revocation,
):
    db, window, root = rig(tmp_path)
    fake_kernel(monkeypatch, lambda: (volume(),))
    before = len(FakeNative.created)
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    _, discover, done, err, _opts = window.runner.calls[0]
    selected = discover()
    assert selected == volume()
    if revocation == "identity":
        window.service.current_identity = NS(identity_id="bob")
    elif revocation == "account":
        window._account_context_generation += 1
    elif revocation == "generation":
        window._identity_context_generation += 1
    elif revocation == "device":
        window._reader_selected_device_id = "reader-bob"
    elif revocation == "route":
        window._route_display_token = object()
    else:
        binding = db.device_binding_get("reader-alice")
        db.device_binding_put({
            **binding, "metadata": {"usb_fingerprint": "uuid-other-0002"},
        })
    done(selected)
    err(RuntimeError("PRIVATE_TOKEN"))
    assert len(window.runner.calls) == 1
    assert len(FakeNative.created) == before
    assert getattr(window, "_reader_usb_preview_chooser", None) is None


def test_exact_binding_core_attestation_does_not_trust_uuid_or_mountpath_alone(
    tmp_path, monkeypatch,
):
    db, window, root = rig(tmp_path)
    fake_kernel(monkeypatch, lambda: (volume(),))
    assert _discover_mounted_binding(
        storage=db, identity_id="alice", device_id="reader-alice",
        fingerprint=UUID,
    ) == volume()
    # Even an identical UUID+path isn't enough if the Core mount generation
    # or physical filesystem facts no longer match the fresh discovery.
    monkeypatch.setattr(
        linux_mount, "observe_linux_usb_mount",
        lambda **kwargs: linux_mount.LinuxUsbMount(
            identity_id="alice", device_id="reader-alice",
            usb_fingerprint=UUID, mountpoint=MOUNT,
            filesystem="exfat", major=8, minor=17,
            checked_at=int(time()), mount_id=999,
        ),
    )
    assert _discover_mounted_binding(
        storage=db, identity_id="alice", device_id="reader-alice",
        fingerprint=UUID,
    ) is None


def test_binding_revoked_between_discovery_callback_and_scan_worker_refuses_io(
    tmp_path, monkeypatch,
):
    db, window, root = rig(tmp_path)
    fake_kernel(monkeypatch, lambda: (volume(),))
    scanned = []
    monkeypatch.setattr(
        worker, "inspect_usb_dry_run",
        lambda **kwargs: scanned.append(kwargs),
    )
    request_reader_usb_preview(window, object(), Gtk=FAKE_GTK)
    _label, discover, done, _err, _options = window.runner.calls[0]
    selected = discover()
    done(selected)
    assert len(window.runner.calls) == 2
    binding = db.device_binding_get("reader-alice")
    db.device_binding_put({
        **binding, "metadata": {"usb_fingerprint": "uuid-swapped-0003"},
    })
    _label, scan, _ok, _fail, _options = window.runner.calls[1]
    with pytest.raises(ValueError, match="reader scope changed"):
        scan()
    assert scanned == []


def test_worker_closures_never_read_gtk_window_from_task_thread():
    from pathlib import Path
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/reader_usb_preview_actions.py"
    ).read_text(encoding="utf-8")
    scan_worker = source.split("        def work():", 1)[1].split(
        "        window.runner.submit(", 1
    )[0]
    discover_worker = source.split("    def discover():", 1)[1].split(
        "    def discovered(", 1
    )[0]
    import re
    # root_current() is a pure config revalidation, not a GTK window call.
    assert re.search(r"(?<![A-Za-z_])current\\(\\)", scan_worker) is None
    assert re.search(r"(?<![A-Za-z_])current\\(\\)", discover_worker) is None
    assert "window." not in scan_worker
    assert "window." not in discover_worker
