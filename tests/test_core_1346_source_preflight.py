"""P1 #1346/#1347: Kobo previews are scoped, offline and explicitly requested."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.gui.sync_view import sync_local_preflight_presentation
from cifereca.plugin_api import SyncResult, SynchronizationBroker
from tests.test_core039_contract_surface import Account, FakeService


COUNTS = {
    "already_local_linked": 3, "already_local_verified": 0,
    "needs_reconciliation": 2, "attention_required": 0,
    "blocked_scope": 0, "blocked_unlinked": 0,
    "not_downloadable": 0, "candidate_unverified": 1,
    "potentially_missing": 0, "invalid_manifest_items": 0,
}


def _broker(*, broken_owner=False):
    service = FakeService()
    service.accounts.append(Account(
        "reader-2", "kobo", "vault-secret", "kobo-reader",
        "PRIVATE KOBO ACCOUNT",
    ))
    kobo = service.accounts[-1]
    service._sessions[service._session_key("kobo", "vault-secret")] = NS(
        connected=True, capabilities=NS(personal_library=True, backup=True),
    )
    service.storage.identity_for_connection = lambda provider, credential: (
        "other-identity" if broken_owner else "reader-2"
    )
    calls = []

    def preview(credential, *, refresh_manifest, verify_local_files):
        calls.append((credential, refresh_manifest, verify_local_files))
        return {
            "known_item_count": 6,
            "counts": dict(COUNTS),
            "manifest_refreshed": False,
            "requires_manifest_validation": True,
            "acquisition_authorized": False,
            "source_id": "PRIVATE_SOURCE_ID",
            "examples": [{"item_ref": "PRIVATE_BOOK_ID"}],
            "local_path": "/home/PRIVATE/secret.epub",
            "message": "Authorization: Bearer PRIVATE_KEY",
        }

    service.kobo_backup_preview = preview
    broker = SynchronizationBrokerImpl(service)
    target = next(row for row in broker.sources() if row.provider_id == "kobo")
    return service, broker, target, calls


def test_preflight_only_on_explicit_call_and_only_for_kobo_source():
    service, broker, target, calls = _broker()
    assert isinstance(broker, SynchronizationBroker)
    assert calls == []
    assert "local_materialization" in target.sync_capabilities
    assert broker.sources("reader-2")[0].source_id == target.source_id
    other = broker.sources("alice")[0]
    unsupported = broker.preview_local_materialization(other.source_id)
    assert not unsupported.ok
    assert unsupported.facts["status"] == "not_supported"
    assert not calls and not service.storage.values
    receipt = broker.preview_local_materialization(target.source_id)
    assert receipt.ok and receipt.changed == 0
    assert calls == [("vault-secret", False, False)]
    assert receipt.facts["known_item_count"] == 6
    assert receipt.facts["counts"] == COUNTS
    assert receipt.facts["acquisition_authorized"] is False
    assert receipt.facts["requires_manifest_validation"] is True
    assert receipt.facts["manifest_refreshed"] is False
    for private in ("PRIVATE", "vault-secret", "reader-2", "Authorization"):
        assert private not in repr(receipt)
    assert service.calls == [] and service.storage.values == {}
    label = sync_local_preflight_presentation(receipt)
    assert "Notices connues : 6" in label
    assert "liens de sauvegarde repérés : 3" in label
    assert "Déclarations manuelles : non évaluées" in label
    assert "à rapprocher : 2" in label
    assert "aucun téléchargement autorisé" in label


def test_preflight_may_check_local_files_but_never_refreshes_remote_manifest():
    _service, broker, target, calls = _broker()
    receipt = broker.preview_local_materialization(target.source_id, verify_local_files=True)
    assert receipt.ok
    assert receipt.facts["files_verified"] is True
    assert calls == [("vault-secret", False, True)]
    assert "vérifiés par empreinte" in sync_local_preflight_presentation(receipt)


def test_preflight_blocks_disabled_mismatched_or_unknown_sources_without_io():
    service, broker, target, calls = _broker()
    broker.set_enabled(target.source_id, False)
    first = broker.preview_local_materialization(target.source_id)
    assert not first.ok and first.facts["status"] == "source_disabled"
    assert not calls
    other_service, other_broker, other_target, other_calls = _broker(broken_owner=True)
    second = other_broker.preview_local_materialization(other_target.source_id)
    assert second.facts["status"] == "invalid_source"
    assert not other_calls
    third = broker.preview_local_materialization("PRIVATE_OR_UNKNOWN_SOURCE")
    assert third.facts["status"] == "unknown_source"
    assert "PRIVATE_OR_UNKNOWN_SOURCE" not in repr(third)
    assert not calls


def test_preflight_untrusted_local_report_or_errors_fail_closed():
    service, broker, target, calls = _broker()
    correct = service.kobo_backup_preview

    for override in (
        {"acquisition_authorized": True},
        {"manifest_refreshed": True},
        {"requires_manifest_validation": False},
        {"counts": {**COUNTS, "already_local_linked": True}},
        {"counts": {**COUNTS, "needs_reconciliation": "PRIVATE_TOKEN"}},
        {"known_item_count": -1},
    ):
        def corrupt(*args, **kwargs):
            raw = correct(*args, **kwargs)
            raw.update(override)
            return raw
        service.kobo_backup_preview = corrupt
        response = broker.preview_local_materialization(target.source_id)
        assert not response.ok
        assert response.facts["status"] == "preview_unavailable"
        assert "PRIVATE" not in repr(response)
        service.kobo_backup_preview = correct

    def raises_secret(*args, **kwargs):
        raise RuntimeError("https://PRIVATE_VAULT/?token=PRIVATE_TOKEN")
    service.kobo_backup_preview = raises_secret
    response = broker.preview_local_materialization(target.source_id)
    assert response.facts["status"] == "preview_unavailable"
    assert "PRIVATE" not in repr(response)
    assert broker.preview_local_materialization(target.source_id, verify_local_files="yes").facts[
        "status"
    ] == "invalid_options"


def test_preflight_presentation_does_not_echo_untrusted_values():
    bad = SyncResult(
        ok=True, facts={
            "status": "preview_only", "counts": {**COUNTS, "blocked_scope": "PRIVATE_DATA"},
            "known_item_count": 1, "read_only": True,
            "acquisition_authorized": False, "requires_manifest_validation": True,
        },
    )
    msg = sync_local_preflight_presentation(bad)
    assert msg.startswith("Aperçu indisponible.")
    assert "PRIVATE" not in msg
    unknown = SyncResult(ok=False, warnings=("PRIVATE_TOKEN",),
                         facts={"status": "PRIVATE_TOKEN"})
    assert "PRIVATE" not in sync_local_preflight_presentation(unknown)


def test_sync_page_preview_is_explicit_identity_bound_and_not_an_acquisition():
    code = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/sync_controller.py"
    ).read_text(encoding="utf-8")
    assert 'source.provider_id == "kobo"' in code
    assert '"local_materialization" in source.sync_capabilities' in code
    assert 'broker.preview_local_materialization(sid)' in code
    assert 'sync_local_preflight_presentation(result)' in code
    assert 'getattr(self, "_sync_preview_generation", None) == version' in code
    assert 'self.service.current_identity' in code
    assert 'kind="sync-preflight"' in code
    assert 'run_kobo_backup(' not in code.split('def run_opening_sync(', 1)[0]
    assert 'sync_kobo_backup(' not in code.split('def run_opening_sync(', 1)[0]


def test_p0_2797_zero_backup_links_do_not_disprove_saved_user_declarations():
    from cifereca.plugin_api import SyncResult

    result = SyncResult(ok=True, facts={
        "status": "preview_only", "known_item_count": 9,
        "counts": {**COUNTS, "already_local_linked": 0, "already_local_verified": 0},
        "read_only": True, "acquisition_authorized": False,
        "requires_manifest_validation": True,
        "manifest_refreshed": False, "files_verified": False,
    })
    label = sync_local_preflight_presentation(result)
    assert "liens de sauvegarde repérés : 0" in label
    assert "Déclarations manuelles : non évaluées" in label
    assert "0 déclaration" not in label
    assert "Manifeste distant non actualisé" in label
    assert "aucun téléchargement autorisé" in label


@pytest.mark.parametrize("value", ["5", -1, 1_000_001, True, False, {"secret": "PRIVATE"}, []])
def test_p0_2797_untrusted_declared_counter_fails_closed(value):
    from cifereca.plugin_api import SyncResult

    result = SyncResult(ok=True, facts={
        "status": "preview_only", "known_item_count": 9,
        "counts": {**COUNTS, "already_local_declared": value},
        "read_only": True, "acquisition_authorized": False,
        "requires_manifest_validation": True,
        "manifest_refreshed": True, "files_verified": True,
    })
    label = sync_local_preflight_presentation(result)
    assert label == "Aperçu indisponible. Aucun téléchargement autorisé."
    assert "PRIVATE" not in label


@pytest.mark.parametrize("remote, local", [
    (True, False), (False, True), (False, False),
])
def test_p0_2797_declarations_not_counted_without_both_checks(remote, local):
    from cifereca.plugin_api import SyncResult

    result = SyncResult(ok=True, facts={
        "status": "preview_only", "known_item_count": 9,
        "counts": {**COUNTS, "already_local_declared": 5},
        "read_only": True, "acquisition_authorized": False,
        "requires_manifest_validation": True,
        "manifest_refreshed": remote, "files_verified": local,
    })
    assert sync_local_preflight_presentation(result) == (
        "Aperçu indisponible. Aucun téléchargement autorisé."
    )


def test_p0_2797_valid_verified_declarations_separated_from_backup_links():
    from cifereca.plugin_api import SyncResult

    result = SyncResult(ok=True, facts={
        "status": "preview_only", "known_item_count": 9,
        "counts": {**COUNTS, "already_local_linked": 0,
                   "already_local_verified": 0, "already_local_declared": 5},
        "read_only": True, "acquisition_authorized": False,
        "requires_manifest_validation": True,
        "manifest_refreshed": True, "files_verified": True,
    })
    label = sync_local_preflight_presentation(result)
    assert "liens de sauvegarde repérés : 0" in label
    assert "Déclarations manuelles vérifiées pour ce compte : 5" in label
    assert "Manifeste distant actualisé" in label
    assert "aucun téléchargement autorisé" in label
