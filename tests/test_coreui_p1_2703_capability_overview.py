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
