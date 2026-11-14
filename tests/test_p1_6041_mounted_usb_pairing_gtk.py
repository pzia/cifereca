"""#6041: mounted-volume-first GTK pairing, separate expert UUID mode.

Synthetic Core enumerator and fake GTK; real SQLite in temp fixtures.
Never mount/read/write a physical USB stick or activate device examination.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import device_usb_discovery as discovery
from tests.test_p1_2854_reader_pairing_gtk import open_dialog, rig


def volume(*, uuid="ABCD-1234", label="SanDisk",
           mount="/media/alice/SanDisk", mid=189, minor=17):
    return discovery.MountedUsbVolume(
        usb_fingerprint=uuid, mountpoint=Path(mount),
        filesystem="exfat", label=label,
        capacity_bytes=500_000_000_000, major=8, minor=minor,
        mount_id=mid,
    )


def test_sandisk_is_selected_by_name_and_paired_disabled_without_entering_uuid(
    tmp_path, monkeypatch,
):
    db, window, errors = rig(tmp_path)
    calls = []
    monkeypatch.setattr(
        discovery, "discover_linux_usb_volumes",
        lambda: (calls.append("read only") or (volume(),)),
    )
    previous = deepcopy(db.device_bindings())
    intent = deepcopy(db.device_placements())
    dialog = open_dialog(window, expert=False)
    assert dialog.expert_field.get_active() is False
    assert dialog.uuid_field.visible is False
    assert dialog.volume_choice.visible
    assert dialog.volume_choice.sensitive
    assert dialog.volume_choice.get_selected() == 0xFFFFFFFF
    assert len(dialog.volume_choice.model) == 1
    assert "SanDisk" in dialog.volume_choice.model[0]
    assert "exFAT" in dialog.volume_choice.model[0]
    assert "Go" in dialog.volume_choice.model[0]
    assert "ABCD-1234" not in dialog.volume_choice.model[0]
    assert db.device_bindings() == previous
    dialog.volume_choice.set_selected(0)
    assert dialog.name_field.get_text() == "SanDisk"
    dialog.name_field.set_text("Ma liseuse USB")
    dialog.respond("confirm")
    row = db.device_binding_get(window._reader_selected_device_id)
    assert row["label"] == "Ma liseuse USB"
    assert row["metadata"]["usb_fingerprint"] == "ABCD-1234"
    assert row["enabled"] is False
    assert row["identity_id"] == "alice"
    assert len(calls) == 2  # initial scan, explicit fresh admission at confirm
    assert db.device_placements() == intent
    assert not errors


@pytest.mark.parametrize("scenario", [
    "none", "invalid-selection", "unplug", "replug", "replaced-uuid",
    "other-profile", "changed-generation",
])
def test_cannot_pair_unselected_stale_or_replaced_volume(
    tmp_path, monkeypatch, scenario,
):
    db, window, errors = rig(tmp_path)
    shown = [volume()]
    monkeypatch.setattr(discovery, "discover_linux_usb_volumes",
                        lambda: tuple(shown))
    dialog = open_dialog(window, expert=False)
    previous = deepcopy(db.device_bindings())
    selected_before = window._reader_selected_device_id
    if scenario != "none":
        dialog.volume_choice.set_selected(0)
    if scenario == "invalid-selection":
        dialog.volume_choice.set_selected(-1)
    if scenario == "unplug":
        shown.clear()
    if scenario == "replug":
        shown[:] = [volume(mid=190)]
    if scenario == "replaced-uuid":
        shown[:] = [volume(uuid="BBBB-5678")]
    if scenario == "other-profile":
        window.service.current_identity = NS(identity_id="bob")
    if scenario == "changed-generation":
        window._identity_context_generation += 1
    dialog.respond("confirm")
    assert db.device_bindings() == previous
    assert db.device_placements() == []
    assert window._reader_selected_device_id == selected_before
    if scenario not in {"other-profile", "changed-generation"}:
        assert len(errors) == 1
        assert "ABCD-1234" not in repr(errors)
    else:
        assert not errors


def test_explicit_refresh_requires_new_selection_and_does_not_retain_uuid(
    tmp_path, monkeypatch,
):
    db, window, errors = rig(tmp_path)
    shown = [volume()]
    monkeypatch.setattr(discovery, "discover_linux_usb_volumes",
                        lambda: tuple(shown))
    dialog = open_dialog(window, expert=False)
    dialog.volume_choice.set_selected(0)
    shown[:] = [volume(uuid="AAAA-9876", label="Kobo",
                      mount="/media/alice/Kobo", mid=200, minor=18)]
    dialog.refresh_button.click()
    assert dialog.volume_choice.get_selected() == 0xFFFFFFFF
    assert "Kobo" in dialog.volume_choice.model[0]
    assert "ABCD-1234" not in str(dialog.volume_choice.model)
    dialog.volume_choice.set_selected(0)
    assert dialog.name_field.get_text() == "Kobo"  # Auto-prefill is refreshed.
    dialog.respond("confirm")
    row = db.device_binding_get(window._reader_selected_device_id)
    assert row["metadata"]["usb_fingerprint"] == "AAAA-9876"
    assert row["label"] == "Kobo"  # UUID remains the identity, not the label.
    assert not errors


def test_multiple_volumes_no_automatic_first_choice_and_exact_second_uuid(
    tmp_path, monkeypatch,
):
    db, window, errors = rig(tmp_path)
    options = (
        volume(uuid="AAAA-1111", label="Reader A", mount="/media/alice/A"),
        volume(uuid="BBBB-2222", label="Reader B",
               mount="/media/alice/B", mid=200, minor=18),
    )
    monkeypatch.setattr(discovery, "discover_linux_usb_volumes",
                        lambda: options)
    dialog = open_dialog(window, expert=False)
    assert dialog.volume_choice.get_selected() == 0xFFFFFFFF
    dialog.volume_choice.set_selected(1)
    assert dialog.name_field.get_text() == "Reader B"
    dialog.respond("confirm")
    row = db.device_binding_get(window._reader_selected_device_id)
    assert row["metadata"]["usb_fingerprint"] == "BBBB-2222"
    assert row["label"] == "Reader B"
    assert not errors


def test_empty_volume_list_offers_expert_mode_and_preserves_original_path(
    tmp_path, monkeypatch,
):
    db, window, errors = rig(tmp_path)
    monkeypatch.setattr(discovery, "discover_linux_usb_volumes", lambda: ())
    dialog = open_dialog(window, expert=False)
    volume_label = dialog.extra.children[0]
    assert dialog.volume_choice.sensitive is False
    assert "Aucun volume" in dialog.volume_status.text
    assert dialog.uuid_field.visible is False
    assert volume_label.visible is True
    dialog.expert_field.set_active(True)
    assert dialog.uuid_field.visible
    assert not dialog.volume_choice.visible
    assert volume_label.visible is False
    dialog.expert_field.set_active(False)
    assert volume_label.visible is True
    assert dialog.volume_choice.visible is True
    dialog.expert_field.set_active(True)
    assert volume_label.visible is False
    dialog.name_field.set_text("Liseuse hors liste")
    dialog.uuid_field.value = "uuid-alice-6041"
    dialog.respond("confirm")
    row = db.device_binding_get(window._reader_selected_device_id)
    assert row["metadata"]["usb_fingerprint"] == "uuid-alice-6041"
    assert row["enabled"] is False
    assert "support non vérifié" in window._reader_status_label.label
    assert not errors


def test_mounted_volume_already_bound_to_foreign_profile_cannot_be_reclaimed(
    tmp_path, monkeypatch,
):
    db, window, errors = rig(tmp_path)
    db.device_usb_binding_declare(
        identity_id="bob", device_id="bob-reader",
        label="Appareil Bob", usb_fingerprint="ABCD-1234",
    )
    initial = deepcopy(db.device_bindings())
    monkeypatch.setattr(discovery, "discover_linux_usb_volumes",
                        lambda: (volume(),))
    dialog = open_dialog(window, expert=False)
    dialog.volume_choice.set_selected(0)
    dialog.respond("confirm")
    assert db.device_bindings() == initial
    assert len(errors) == 1
    assert "ABCD-1234" not in repr(errors)
