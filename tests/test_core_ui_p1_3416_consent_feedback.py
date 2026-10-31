"""#3416: sync CoreUI logs no credential-bearing broker exception details.

All tests use headless GTK doubles; no external provider, SQLite, network,
source consent change, or file system mutation.
"""
from __future__ import annotations

import logging

import pytest

from tests.test_core_p0_3416_source_sync_consent_ui import rig
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def test_read_exception_is_logged_with_static_code_and_no_sensitive_detail(caplog):
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig()
    secret = "https://private.invalid/?token=PRIVATE_3416_READ"
    def fail_read(source_id):
        reads.append(source_id)
        raise RuntimeError(secret)
    broker.source_sync_consent = fail_read
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        fn(window, broker, source, scope, group)
    assert len(group.children) == 1
    assert group.children[0].suffixes == []
    assert group.children[0].subtitle.startswith("Réglage indisponible")
    assert len(reads) == 1 and not writes and not errors
    assert "Source consent UI read failed" in caplog.text
    assert secret not in caplog.text and "PRIVATE_3416" not in caplog.text


def test_write_exception_is_logged_without_credentials_and_no_retry(caplog):
    fn, window, broker, source, scope, group, state, reads, writes, errors = rig(
        configured=True, enabled=False,
    )
    secret = "https://private.invalid/?token=PRIVATE_3416_WRITE"
    def fail_write(source_id, *, enabled, expected_revision):
        writes.append((source_id, enabled, expected_revision))
        raise RuntimeError(secret)
    broker.set_source_sync_consent = fail_write
    fn(window, broker, source, scope, group)
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        group.children[0].suffixes[0].toggled(True)
    assert writes == [(source.source_id, True, "revision-one")]
    assert len(reads) == len(errors) == 1
    assert errors[0][0].startswith("Le réglage a changé")
    assert "Source consent UI update failed" in caplog.text
    assert secret not in caplog.text and "PRIVATE_3416" not in caplog.text


@pytest.mark.parametrize("provider,sync_capable", [
    ("provider.future", True), ("kobo", True), ("numilog", True),
    ("paris", True), ("bnf-pret", True), ("catalogue.future", False),
])
def test_activation_switch_states_file_consent_is_separate(provider, sync_capable):
    source = _source(
        provider_id=provider, source_id="source-one",
        sync_capable=sync_capable,
    )
    window, broker, calls, build, page = _fixture(sources=[source])
    group = window._sync_sources_box.children[0]
    activation = group.children[0].suffixes[0]
    expected = (
        "autorisation distincte" if sync_capable
        else "n’autorise aucun téléchargement automatique"
    )
    assert expected in activation.tooltip
    assert window.runner.tasks == []
    assert not any(name == "set-enabled" for name, *_ in calls)
