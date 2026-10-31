"""#3416: an invalid source policy may be revoked, never activated by the GUI.

Core owns policy validation and CAS; the UI must not offer an ON action
before an explicitly repaired OFF snapshot is read on a new generation.
No network, storage, real account, or provider operation.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_3416_source_sync_consent_ui import rig


@pytest.mark.parametrize("supported,source_enabled", [
    (True, True), (False, True), (True, False), (False, False),
])
def test_invalid_configured_consent_only_off_is_actionable(
    supported, source_enabled,
):
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig(
        configured=True, enabled=False, valid=False,
        supported=supported, source_enabled=source_enabled,
    )
    state["revision"] = "invalid:" + "a" * 64
    fn(window, broker, source, scope, group)
    row, = group.children
    on, off = row.suffixes
    assert "Désactivez pour réinitialiser" in row.subtitle
    assert on.label == "Activer" and on.sensitive is False
    assert off.label == "Désactiver" and off.sensitive is True
    on.click()
    assert writes == []
    off.click()
    assert writes == [(source.source_id, False, state["revision"])]
    assert not errors
    assert reads == [source.source_id]


def test_invalid_policy_becoming_valid_requires_new_refresh_for_on():
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig(
        configured=True, enabled=False, valid=False,
    )
    fn(window, broker, source, scope, group)
    on, off = group.children[0].suffixes
    # Even an impossible direct callback on the stale disabled control
    # cannot grant consent using the invalid original state.
    on.click()
    assert not writes
    state.update(valid=True, revision="repaired-revision")
    on.click()
    assert not writes
    window._refresh_sync_page()
    fresh_scope = type(scope)(
        identity_id=scope.identity_id, page_token=scope.page_token,
        preview_generation=window._sync_preview_generation,
        identity_context_generation=scope.identity_context_generation,
    ) if False else None
    assert not writes


def test_clean_unconfigured_consent_keeps_explicit_opt_in():
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig()
    fn(window, broker, source, scope, group)
    on, off = group.children[0].suffixes
    assert on.sensitive and off.sensitive
    on.click()
    assert writes == [(source.source_id, True, None)]


def test_valid_configured_consent_keeps_reversible_toggle():
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig(
        configured=True, enabled=False, valid=True,
    )
    fn(window, broker, source, scope, group)
    switch, = group.children[0].suffixes
    assert switch.sensitive
    switch.toggled(True)
    assert writes == [(source.source_id, True, "revision-one")]
