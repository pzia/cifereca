"""#3416: reject incoherent Core consent projections without writing a policy."""
from __future__ import annotations

import pytest

from tests.test_core_p0_3416_source_sync_consent_ui import rig


@pytest.mark.parametrize(("configured", "enabled", "valid", "revision"), [
    (False, True, True, None),
    (False, False, False, None),
    (False, True, False, None),
    (True, True, False, "revision-one"),
])
def test_contradictory_consent_snapshot_never_exposes_write_action(
    configured, enabled, valid, revision,
):
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig()
    state.update(configured=configured, enabled=enabled,
                 valid=valid, revision=revision)
    fn(window, broker, source, scope, group)
    row, = group.children
    assert row.title == "Téléchargement automatique de ce compte"
    assert row.suffixes == []
    assert row.subtitle.startswith("Réglage indisponible.")
    assert reads == [source.source_id]
    assert writes == errors == []
    assert window.runner.tasks == []


def test_valid_configured_policy_still_offers_revocation_after_abi_withdrawal():
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig(
        configured=True, enabled=True, supported=False,
    )
    fn(window, broker, source, scope, group)
    switch, = group.children[0].suffixes
    assert switch.active is True
    assert switch.sensitive is True
    switch.toggled(False)
    assert writes == [(source.source_id, False, "revision-one")]
    assert not errors


def test_unconfigured_valid_policy_still_requires_explicit_choice():
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig()
    fn(window, broker, source, scope, group)
    assert [x.label for x in group.children[0].suffixes] == [
        "Activer", "Désactiver",
    ]
    assert writes == []
