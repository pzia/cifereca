"""P1 #2703: seven read-only source capability families in Settings.

The presentation must never expose an account, credential, right or edition
as a supported action based on provider code declarations or stale snapshots.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.settings_capabilities_overview import (
    append_settings_capability_overview, source_capability_labels,
)
from cifereca.plugin_api import CapabilityEvidence, CapabilityMatrix
from cifereca.services import LibraryService
from tests.test_core_p1_2385_catalogue_source_inventory import _rig


class Widget:
    def __init__(self, label="", xalign=0, orientation=None, spacing=0):
        self.label = label
        self.children = []
        self.expanded = True
        self.title = ""

    def add(self, item):
        self.children.append(item)

    append = add

    def set_child(self, item):
        self.children.append(item)

    def set_title(self, text):
        self.title = text

    def set_wrap(self, value):
        self.wrap = value

    def set_selectable(self, value):
        self.selectable = value

    def set_expanded(self, value):
        self.expanded = value


def _widgets():
    return (
        NS(Label=Widget, Expander=Widget, Box=Widget,
           Orientation=NS(VERTICAL=1)),
        NS(PreferencesGroup=Widget),
    )


def test_real_core_declarations_render_seven_distinct_unknown_or_declared_families():
    service, _calls = _rig()
    values = LibraryService.capability_matrices(service, identity_id="alice")
    rendered = source_capability_labels(values)
    assert len(rendered) == len(values)
    kobo = next(x for x in rendered if x.startswith("kobo\n"))
    for family in ("Catalogue", "Métadonnées", "Droits", "Prêt",
                   "Acquisition", "DRM", "Transversal"):
        assert kobo.count(family + " : ") == 1
    assert "Catalogue : déclaré, non vérifié" in kobo
    assert "Droits : déclaré, non vérifié" in kobo
    assert "DRM : preuve indisponible" in kobo
    assert "Métadonnées : preuve indisponible" in kobo
    assert "acquisition autorisée" not in kobo.casefold()
    assert "a-kobo" not in kobo and "alice" not in kobo


def test_snapshot_account_observation_is_visible_only_as_non_authorizing():
    base = CapabilityMatrix("kobo", (
        CapabilityEvidence(
            "kobo", "personal_inventory.list", "unknown",
            status="unverified", implementation_state="implemented",
            source_kind="code",
        ),
    ))
    observation = CapabilityEvidence(
        "kobo", "personal_inventory.rights", "account",
        field="personal_manifest",
        identity_id="alice", account_id="account-private",
        credential_id="vault-private", status="partial",
        source_kind="account_observation",
        evidence_ref="cifereca.rights.observation/1",
        implementation_state="implemented",
        reason="rights_observation_no_action",
        enabled=True, connected=False,
    )
    matrix = CapabilityMatrix("kobo", base.observations + (observation,))
    text = source_capability_labels((matrix,))[0]
    assert "Droits : observation, action non autorisée" in text
    assert "DRM : preuve indisponible" in text
    assert "account-private" not in text
    assert "vault-private" not in text
    assert "alice" not in text
    assert "personal_manifest" not in text
    assert not observation.authorizes_mutation


def test_real_settings_group_is_collapsed_and_calls_core_once_for_identity():
    core, _calls = _rig()
    matrices = LibraryService.capability_matrices(core, identity_id="bob")
    calls = []
    reader = NS(capability_matrices=lambda identity: (
        calls.append(identity) or matrices
    ))
    Gtk, Adw = _widgets()
    section = Widget()
    assert append_settings_capability_overview(
        section=section, service=reader, Gtk=Gtk, Adw=Adw,
        identity_id="bob",
    ) is True
    assert calls == ["bob"]
    group, = section.children
    assert group.title == "Capacités des sources · lecture seule"
    note, expander = group.children
    assert "Aucune capacité affichée ne confirme un droit" in note.label
    assert expander.label == "Afficher les capacités par source"
    assert expander.expanded is False
    box, = expander.children
    assert len(box.children) == len(matrices)
    assert all("Droits : " in row.label for row in box.children)
    assert all("action non autorisée" in row.label
               or "déclaré, non vérifié" in row.label
               or "preuve indisponible" in row.label
               for row in box.children)


def test_error_and_no_identity_do_not_claim_a_positive_source():
    Gtk, Adw = _widgets()
    section = Widget()
    assert append_settings_capability_overview(
        section=section,
        service=NS(capability_matrices=lambda _identity: (_ for _ in ()).throw(
            RuntimeError("private login and token"),
        )),
        Gtk=Gtk, Adw=Adw, identity_id="alice",
    )
    group, = section.children
    row, = group.children[1].children[0].children
    assert "Aucune source qualifiée" in row.label
    assert "private login" not in row.label
    assert append_settings_capability_overview(
        section=Widget(), service=NS(), Gtk=Gtk, Adw=Adw,
    ) is False


def test_reject_forged_shape_in_pure_presentation():
    with pytest.raises(ValueError, match="capability_invalid_matrix"):
        source_capability_labels((object(),))
    with pytest.raises(ValueError, match="capability_invalid_matrix_collection"):
        source_capability_labels({"provider": "forged"})

def test_explicit_unsupported_is_not_inferred_from_missing_evidence():
    matrix = CapabilityMatrix("synthetic", (
        CapabilityEvidence("synthetic", "loan.renew", "public",
                           status="unsupported", source_kind="code"),
        CapabilityEvidence("synthetic", "catalog.search", "public",
                           status="unverified", source_kind="code"),
    ))
    rendered, = source_capability_labels((matrix,))
    assert "Prêt : non pris en charge (périmètres déclarés)" in rendered
    assert "Catalogue : déclaré, non vérifié" in rendered
    assert "DRM : preuve indisponible" in rendered


def test_missing_identity_never_invokes_navigation_fallback():
    Gtk, Adw = _widgets()
    section = Widget()
    calls = []
    assert append_settings_capability_overview(
        section=section, service=NS(capability_matrices=lambda identity: calls.append(identity)),
        Gtk=Gtk, Adw=Adw, identity_id=None,
    )
    assert not calls


def test_manual_supported_claim_does_not_masquerade_as_provider_observation():
    manual = CapabilityEvidence(
        "synthetic", "loan.borrow", "account", status="supported",
        implementation_state="implemented", source_kind="manual",
        evidence_ref="user.manual.note/1",
    )
    render, = source_capability_labels((CapabilityMatrix("synthetic", (manual,)),))
    assert "Prêt : signalement manuel, non vérifié par le fournisseur" in render
    assert "Prêt : observation, action non autorisée" not in render
    # An independently typed account observation takes precedence but still
    # never claims borrow consent or action authority.
    observed = replace(
        manual, operation="loan.renew", source_kind="account_observation",
        evidence_ref="provider.readonly.observation/1",
    )
    render, = source_capability_labels((
        CapabilityMatrix("synthetic", (manual, observed)),
    ))
    assert "Prêt : observation, action non autorisée" in render


@pytest.mark.parametrize("mutation", [
    ("matrix", "provider_id", "PRIVATE_CREDENTIAL:token"),
    ("matrix", "observations", [1, 2]),
    ("evidence", "status", "supported"),
    ("evidence", "source_kind", "forged_observation"),
    ("evidence", "operation", "loan.borrow\nPRIVATE_TOKEN"),
    ("evidence", "identity_id", "SECRET\nOTHER"),
    ("evidence", "schema_version", True),
])
def test_mutated_frozen_capabilities_fail_closed_before_display(mutation):
    source = CapabilityEvidence(
        "synthetic", "loan.borrow", "unknown",
        status="unverified", implementation_state="implemented",
        source_kind="code", reason="code_declaration_only",
    )
    matrix = CapabilityMatrix("synthetic", (source,))
    target, attr, value = mutation
    object.__setattr__(matrix if target == "matrix" else source, attr, value)
    with pytest.raises(ValueError, match="capability_invalid_matrix"):
        source_capability_labels((matrix,))


def test_settings_does_not_leak_bad_capability_after_snapshot_mutation():
    observed = CapabilityEvidence(
        "synthetic", "loan.borrow", "account", status="supported",
        implementation_state="implemented",
        source_kind="account_observation", evidence_ref="proof/1",
    )
    matrix = CapabilityMatrix("synthetic", (observed,))
    object.__setattr__(matrix, "provider_id", "PRIVATE_TOKEN:SECRET")
    Gtk, Adw = _widgets()
    section = Widget()
    assert append_settings_capability_overview(
        section=section, service=NS(capability_matrices=lambda _id: (matrix,)),
        Gtk=Gtk, Adw=Adw, identity_id="alice",
    )
    label = section.children[0].children[1].children[0].children[0].label
    assert "Aucune source qualifiée" in label
    assert "PRIVATE_TOKEN" not in label
    assert "SECRET" not in label


def test_installed_backup_flag_is_visible_only_as_transversal_declaration():
    """Installed artifact.backup does not establish a completed real backup."""
    from cifereca.plugin_api import (
        ProviderCapabilities, ProviderDescriptor, declared_provider_matrix,
    )

    descriptor = ProviderDescriptor(
        id="synthetic", display_name="Synthetic", description="offline",
        state="ready", capabilities=ProviderCapabilities(backup=True),
    )
    matrix = declared_provider_matrix(descriptor)
    text, = source_capability_labels((matrix,))
    assert "Transversal : déclaré, non vérifié" in text
    assert "Acquisition : non pris en charge (périmètres déclarés)" in text
    assert "Transversal : observation" not in text
    assert "sauvegarde autorisée" not in text


def test_sync_and_backup_evidence_share_one_read_only_transversal_family():
    matrix = CapabilityMatrix("synthetic", (
        CapabilityEvidence(
            "synthetic", "artifact.backup", "unknown",
            status="unverified", source_kind="code",
        ),
        CapabilityEvidence(
            "synthetic", "sync.inventory", "account", status="partial",
            source_kind="account_observation", implementation_state="implemented",
            evidence_ref="synthetic.local.inventory/1", reason="partial_only",
        ),
    ))
    text, = source_capability_labels((matrix,))
    assert text.count("Transversal : ") == 1
    assert "Transversal : observation, action non autorisée (partielle ou mixte)" in text
    assert "synthetic.local.inventory/1" not in text


@pytest.mark.parametrize(("family", "operation"), [
    ("Catalogue", "catalog.search"),
    ("Métadonnées", "metadata.enrichment"),
    ("Droits", "personal_inventory.rights"),
    ("Prêt", "loan.inventory"),
    ("Acquisition", "acquisition.download"),
    ("DRM", "drm.license"),
    ("Transversal", "sync.scheduled_refresh"),
])
@pytest.mark.parametrize(("status", "source_kind", "expected"), [
    ("supported", "account_observation", "observation, action non autorisée"),
    ("partial", "account_observation",
     "observation, action non autorisée (partielle ou mixte)"),
    ("unsupported", "code", "non pris en charge (périmètres déclarés)"),
    ("unverified", "code", "déclaré, non vérifié"),
])
def test_each_family_distinguishes_four_synthetic_evidence_states(
    family, operation, status, source_kind, expected,
):
    """Exercise the UI contract, not live evidence or new provider capabilities."""
    row = CapabilityEvidence(
        "synthetic", operation, "account",
        status=status, source_kind=source_kind,
        implementation_state=("not_applicable" if status == "unsupported"
                              else "implemented"),
        evidence_ref=("fixture/1" if source_kind == "account_observation" else ""),
        reason="synthetic_partial" if status == "partial" else "",
        identity_id="private-identity", account_id="private-account",
        credential_id="private-credential", edition_id="private-edition",
        enabled=True, connected=True,
    )
    render, = source_capability_labels((CapabilityMatrix("synthetic", (row,)),))
    assert f"{family} : {expected}" in render.split("\n", 1)[1].split("\nOpérations", 1)[0].split(" · ")
    assert not row.authorizes_mutation
    assert "private-" not in render
    assert "fixture/1" not in render
    missing, = source_capability_labels((CapabilityMatrix("synthetic", ()),))
    assert f"{family} : preuve indisponible" in missing.split("\n", 1)[1].split(" · ")


def test_disconnected_observation_is_explicitly_historical_not_live():
    """A persisted rights report must not masquerade as current availability."""
    disconnected = CapabilityEvidence(
        "synthetic", "personal_inventory.rights", "account",
        identity_id="private-owner", account_id="private-card",
        status="partial", implementation_state="implemented",
        source_kind="account_observation", evidence_ref="rights.snapshot/1",
        reason="historical_snapshot", enabled=True, connected=False,
    )
    label, = source_capability_labels((CapabilityMatrix("synthetic", (disconnected,)),))
    assert "Droits : observation, action non autorisée" in label
    assert "source déconnectée, observation historique" in label
    assert "private-owner" not in label and "private-card" not in label
    assert "rights.snapshot/1" not in label
    connected = replace(disconnected, connected=True)
    label, = source_capability_labels((CapabilityMatrix("synthetic", (connected,)),))
    assert "observation historique" not in label
    # Unknown connection is not asserted to be offline either.
    unknown = replace(disconnected, connected=None)
    label, = source_capability_labels((CapabilityMatrix("synthetic", (unknown,)),))
    assert "source déconnectée" not in label


def test_mixed_or_unknown_connection_observations_are_not_presented_as_current():
    """An observed family can combine several distinct account connections."""
    base = CapabilityEvidence(
        "synthetic", "personal_inventory.rights", "account",
        identity_id="private-owner", account_id="private-card",
        status="supported", implementation_state="implemented",
        source_kind="account_observation", evidence_ref="rights.snapshot/1",
        enabled=True, connected=True,
    )
    disconnected = replace(
        base, operation="personal_inventory.list", connected=False,
        account_id="private-other",
    )
    unknown = replace(
        base, operation="personal_inventory.list", connected=None,
        account_id="private-other",
    )
    for rows in ((base, disconnected), (base, unknown), (unknown,)):
        label, = source_capability_labels((CapabilityMatrix("synthetic", rows),))
        assert "Droits : observation, action non autorisée" in label
        assert "état de connexion mixte ou inconnu" in label
        assert "private-" not in label and "rights.snapshot/1" not in label
    label, = source_capability_labels((CapabilityMatrix("synthetic", (base,)),))
    assert "état de connexion mixte ou inconnu" not in label


def test_coreui_uses_canonical_family_for_existing_sync_and_rights_operations():
    matrix = CapabilityMatrix("synthetic", tuple(
        CapabilityEvidence(
            "synthetic", operation, "account", status="partial",
            source_kind="account_observation", implementation_state="implemented",
            evidence_ref="synthetic.readonly/1", reason="read_only",
        ) for operation in (
            "rights.observe", "account.profile", "sync.entitlements",
            "sync.remote_inventory", "booking.list",
            "sync.local_materialization", "transform.convert",
            "cache.refresh", "sync.scheduled_refresh",
        )
    ))
    rendered, = source_capability_labels((matrix,))
    for family in ("Droits", "Prêt", "Acquisition", "DRM", "Transversal"):
        assert f"{family} : observation, action non autorisée" in rendered
    assert "Catalogue : preuve indisponible" in rendered
    assert "Métadonnées : preuve indisponible" in rendered
    assert "synthetic.readonly/1" not in rendered


def test_settings_lists_exact_operations_scopes_and_provenance_without_private_ids():
    rows = (
        CapabilityEvidence(
            "synthetic", "loan.borrow", "account", status="partial",
            implementation_state="implemented", source_kind="account_observation",
            mode="server", evidence_ref="private/proof",
            reason="provider private reason", identity_id="alice-private",
            account_id="account-private", credential_id="token-private",
            edition_id="edition-private", enabled=False, connected=False,
        ),
        CapabilityEvidence(
            "synthetic", "catalog.search", "public", status="unverified",
            implementation_state="implemented", source_kind="code",
            mode="local_postfilter_partial",
        ),
    )
    label, = source_capability_labels((CapabilityMatrix("synthetic", rows),))
    assert ("loan.borrow [account] : partial (account_observation, server)"
            " · source désactivée · connexion absente") in label
    assert ("catalog.search [public] : unverified "
            "(code, local_postfilter_partial)") in label
    assert label.index("catalog.search [public]") < label.index("loan.borrow [account]")
    for private in ("alice-private", "account-private", "token-private",
                    "edition-private", "private/proof", "provider private reason"):
        assert private not in label
    assert "Prêt : observation, action non autorisée" in label


def test_p1_2703_same_operation_code_and_account_observation_keep_distinct_scopes():
    """A provider declaration is not a current borrower-scoped observation."""
    matrix = CapabilityMatrix("synthetic-provider", (
        CapabilityEvidence(
            "synthetic-provider", "loan.borrow", "provider",
            status="unverified", implementation_state="implemented",
            source_kind="code",
        ),
        CapabilityEvidence(
            "synthetic-provider", "loan.borrow", "account",
            identity_id="alice", account_id="private-borrower",
            credential_id="private-credential",
            status="partial", implementation_state="implemented",
            source_kind="account_observation", reason="partial_account_observation",
            enabled=False, connected=False,
        ),
    ))
    text = source_capability_labels((matrix,))[0]
    assert "Prêt : observation, action non autorisée" in text
    assert "loan.borrow [provider] : unverified (code" in text
    assert "loan.borrow [account] : partial (account_observation" in text
    assert "source désactivée" in text and "connexion absente" in text
    assert "private-borrower" not in text
    assert "private-credential" not in text
    assert "alice" not in text
