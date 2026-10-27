"""#2703: Core's live-service *declarations* inventory is safe and identity-bound.

All providers/accounts are synthetic. An installed plugin, connected search
or discovered entitlement does not authorize a refresh, loan or acquisition.
"""
from __future__ import annotations

from dataclasses import asdict

from cifereca.core.capability_inventory import capability_matrices_for_identity
from cifereca.services import LibraryService
from cifereca.plugin_api import CapabilityMatrix
from tests.test_core_p1_2385_catalogue_source_inventory import _rig


def _lookup(rows):
    return {row.provider_id: row for row in rows}


def test_core_service_exposes_only_enabled_registered_per_identity_sources():
    service, _ = _rig()
    matrices = LibraryService.capability_matrices(service, identity_id="alice")
    names = [row.provider_id for row in matrices]
    assert names == [
        "bnf-catalogue", "ebooksgratuits", "gallica", "gutenberg",
        "kobo", "numilog", "paris", "rights-only",
    ]
    assert all(type(row) is CapabilityMatrix for row in matrices)
    assert "technical" not in names
    assert "numilog-catalog" not in names
    assert "disabled" not in names

    gallica = _lookup(matrices)["gallica"]
    public = gallica.exact("catalog.search", "public", identity_id="alice")
    assert public.status == "unverified"
    assert public.connected is True
    assert public.source_kind == "code"
    assert gallica.exact("catalog.refresh", "public",
                         identity_id="alice").status == "unsupported"
    assert gallica.exact("personal_inventory.list", "public",
                         identity_id="alice").status == "unsupported"
    assert public.authorizes_mutation is False

    paris = _lookup(matrices)["paris"]
    authenticated = paris.exact(
        "catalog.search", "authenticated", identity_id="alice",
    )
    assert authenticated.status == "unverified"
    assert authenticated.connected is False
    assert authenticated.reason == "declared_not_connected"
    assert paris.exact("loan.borrow", "unknown").status == "unsupported"
    assert paris.exact("catalog.search", "authenticated",
                       identity_id="bob") is None
    assert paris.exact("catalog.search", "authenticated",
                       field="title", identity_id="alice") is None


def test_second_identity_is_isolated_even_when_first_has_connected_session():
    service, _ = _rig()
    bob = _lookup(LibraryService.capability_matrices(service, "bob"))
    assert "kobo" not in bob
    assert "ebooksgratuits" not in bob
    assert "numilog" in bob
    assert bob["numilog"].exact("catalog.search", "authenticated",
                               identity_id="bob").connected is False
    assert bob["rights-only"].exact(
        "personal_inventory.list", "authenticated", identity_id="bob",
    ).connected is True
    assert not any(
        row.identity_id == "alice"
        for matrix in bob.values()
        for row in matrix.observations
        if row.scope != "unknown"
    )


def test_unknown_identity_does_not_query_source_accounts_or_emit_rows():
    service, calls = _rig()
    for identity in ("unknown", "bob/private", "", "alice\x00unexpected"):
        assert LibraryService.capability_matrices(service, identity) == ()
    assert not any(c[0] == "accounts" for c in calls)
    service.current_identity = None
    assert LibraryService.capability_matrices(service) == ()


def test_default_active_identity_and_read_only_no_io_or_persisted_changes():
    service, _ = _rig()
    service.search = lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must never query a provider")
    )
    service.synchronize = service.acquire_direct_item = lambda *a, **kw: (
        _ for _ in ()
    ).throw(AssertionError("must never mutate or acquire"))
    matrices = capability_matrices_for_identity(service)
    assert matrices == LibraryService.capability_matrices(service)
    assert len(matrices) == 8
    assert all(not record.authorizes_mutation
               for matrix in matrices for record in matrix.observations)
    assert not any(record.status == "supported"
                   for matrix in matrices for record in matrix.observations)
    assert all(record.source_kind == "code"
               for matrix in matrices for record in matrix.observations)
    assert not any(record.evidence_ref
                   for matrix in matrices for record in matrix.observations)
    assert len({m.provider_id for m in matrices}) == len(matrices)


def test_ui_projection_remains_serializable_and_only_reports_code_evidence():
    service, _ = _rig()
    state = [asdict(matrix) for matrix in LibraryService.capability_matrices(service)]
    import json
    rendered = json.dumps(state)
    assert '"provider_id": "gallica"' in rendered
    assert '"status": "unverified"' in rendered
    assert '"catalog.refresh"' in rendered
    assert '"status": "supported"' not in rendered
    assert "Private reader name" not in rendered
    assert "login" not in rendered
    assert "password" not in rendered
    assert "a-numilog" not in rendered
    assert "a-kobo" not in rendered
