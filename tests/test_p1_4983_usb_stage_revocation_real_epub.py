"""P1 USB: a real EPUB stage must never publish after authority is revoked.

The source EPUB, transport staging FD, journal and directory are all real.
Only the Linux block-device attestation and the initial positive source policy
are synthetic, as in the already merged Core copy/observe/remove fixture.
"""
from __future__ import annotations

from hashlib import sha256

import pytest

from tests.test_p1_4985_usb_copy_observe_remove_real_fs import cycle
from tests.test_p1_2854_usb_live_source import live_args
from tests.test_p1_2854_usb_copy_preparation import copy_review


@pytest.mark.parametrize("revocation", (
    "policy_revoked",
    "profile_aba",
    "transport_reloaded",
    "wish_revoked",
))
def test_revocation_after_real_stage_write_never_publishes_epub(
    cycle, revocation,
):
    rig = cycle
    expected = rig.source.read_bytes()
    assert expected.startswith(b"PK")
    assert sha256(expected).hexdigest() == rig.consent.sha256
    assert not rig.final.exists()

    transport = rig.service._plugin_manager.services("storage.target")[0]
    stage = rig.final.parent / (
        ".cifereca-usb-" + rig.consent.operation_id + ".part"
    )
    stage_proofs = []

    def writer(payload, append):
        # The actual production stage sink uses os.write() to the pinned FD.
        assert payload == expected
        append(payload)
        written = stage.read_bytes()
        assert written == expected
        assert sha256(written).hexdigest() == rig.consent.sha256
        stage_proofs.append(True)

        # These changes take place AFTER an actual file write, but BEFORE
        # Core has published it under the destination's final filename.
        if revocation == "policy_revoked":
            rig.entitled[0] = False
        elif revocation == "profile_aba":
            previous = rig.active["profile"]
            rig.active["profile"] = ("bob", previous[1], previous[2] + 1)
            rig.active["profile"] = ("alice", previous[1], previous[2] + 2)
        elif revocation == "transport_reloaded":
            transport.registry_generation += 1
        else:
            rig.storage.device_usb_intent_set(
                identity_id=rig.consent.identity_id,
                device_id=rig.consent.device_id,
                artifact_id=rig.consent.artifact_id,
                desired_state="remove",
            )

    transport.service.write_usb_stage = writer
    result = rig.copy(rig.consent)
    assert stage_proofs == [True], "a genuine disk stage must have occurred"
    assert result.status == "recovery_required", (result.status, result.reason)
    assert not rig.final.exists(), "staged bytes are not an accepted USB copy"
    assert rig.source.read_bytes() == expected
    assert stage.read_bytes() == expected
    saved = rig.journal.get(rig.consent.operation_id)
    assert saved is not None and saved.phase == "pending"
    assert saved.receipt is None
    # No replay of the same consent may promote a revoked checkpoint.
    assert rig.copy(rig.consent).status != "completed"
    assert not rig.final.exists()


def test_missing_actual_source_fails_before_stage_or_journal(cycle):
    rig = cycle
    local = rig.source
    original = local.read_bytes()
    local.unlink()
    try:
        result = rig.copy(rig.consent)
        assert result.status != "completed"
        assert not rig.final.exists()
        assert rig.journal.get(rig.consent.operation_id) is None
    finally:
        local.write_bytes(original)
