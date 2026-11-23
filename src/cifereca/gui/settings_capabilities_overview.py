"""P-1 #2703: read-only Settings projection of the Core capability inventory.

No plugin import, provider I/O, GTK global state or permission. Source
declarations are intentionally not rendered as verified availability.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cifereca.plugin_api import CapabilityMatrix
from cifereca.plugin_api.capability_families import _family


_FAMILIES: tuple[tuple[str, str], ...] = (
    ("Catalogue", "catalogue"),
    ("Métadonnées", "metadata"),
    ("Droits", "accounts_rights"),
    ("Prêt", "loans"),
    ("Acquisition", "acquisition"),
    ("DRM", "drm"),
    ("Transversal", "crosscutting"),
)


def source_capability_labels(matrices: Sequence[CapabilityMatrix]) -> tuple[str, ...]:
    """Summarize seven families without confusing code with source evidence.

    A missing operation is UNKNOWN (not unsupported). Only a non-code
    observation can yield a positive/partial factual reading. None of this
    authorizes a network request, acquisition, file reuse or action.
    """
    if type(matrices) not in (tuple, list):
        raise ValueError("capability_invalid_matrix_collection")
    output: list[str] = []
    for matrix in matrices:
        if type(matrix) is not CapabilityMatrix:
            raise ValueError("capability_invalid_matrix")
        # Frozen plugin DTOs can still be altered via object.__setattr__.
        # Revalidate at the consumer before displaying any provider id or
        # treating a claimed observation as evidence.
        try:
            matrix.__post_init__()
            for row in matrix.observations:
                row.__post_init__()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("capability_invalid_matrix") from None
        statuses: list[str] = []
        for label, family in _FAMILIES:
            # Share the public seven-family contract. An unknown legacy
            # sync.* operation stays transversal, never an attested right.
            rows = tuple(row for row in matrix.observations
                         if (_family(row.operation) or (
                             "crosscutting" if row.operation.startswith("sync.")
                             else None)) == family)
            observed = tuple(row for row in rows
                             if row.source_kind in (
                                 "provider_observation", "account_observation",
                             )
                             and row.status in ("supported", "partial"))
            manual = tuple(row for row in rows
                           if row.source_kind == "manual"
                           and row.status in ("supported", "partial"))
            declared = tuple(row for row in rows
                             if row.source_kind == "code"
                             and row.status == "unverified")
            if observed:
                # Even "supported" is only a read-only evidence status; the
                # enabled/connected flags still constrain actual usability.
                state = ("observation, action non autorisée (partielle ou mixte)"
                         if any(row.status != "supported" for row in rows)
                         else "observation, action non autorisée")
                # A cached account observation from a disconnected source is
                # historical evidence, never a claim of current availability.
                # Do not expose account identifiers or producer-supplied notes.
                if all(row.connected is False for row in observed):
                    state += " ; source déconnectée, observation historique"
                elif not all(row.connected is True for row in observed):
                    # Mixed or unknown connection states cannot be promoted
                    # to an entirely connected, current observation.
                    state += " ; état de connexion mixte ou inconnu"
            elif manual:
                # A human-entered report is not an independent provider
                # observation, even if its producer marked it 'supported'.
                state = "signalement manuel, non vérifié par le fournisseur"
            elif declared:
                state = "déclaré, non vérifié"
            elif rows and all(row.status == "unsupported" for row in rows):
                state = "non pris en charge (périmètres déclarés)"
            else:
                state = "preuve indisponible"
            statuses.append(f"{label} : {state}")
        # Provider IDs are canonical plugin registry public keys, not account
        # labels, credentials, source path, edition IDs or vendor notes.
        output.append(matrix.provider_id + "\n" + " · ".join(statuses))
    return tuple(output)


def append_settings_capability_overview(
    *, section: Any, service: Any, Gtk: Any, Adw: Any,
    identity_id: str | None = None,
) -> bool:
    """Append a collapsed, non-interactive snapshot from the Core read API.

    Do not call a provider or register a refresh loop. A source connection
    might change after opening the page; this overview is not an action gate.
    """
    reader = getattr(service, "capability_matrices", None)
    if not callable(reader):
        return False
    try:
        matrices = reader(identity_id) if isinstance(identity_id, str) and identity_id else ()
        labels = source_capability_labels(matrices)
    except Exception:
        labels = ()
    group = Adw.PreferencesGroup()
    group.set_title("Capacités des sources · lecture seule")
    section.append(group)
    explainer = Gtk.Label(
        label=("Déclarations du Core à l’ouverture de cette page. "
               "Aucune capacité affichée ne confirme un droit, un accès "
               "fournisseur ou une opération réalisable."),
        xalign=0,
    )
    explainer.set_wrap(True)
    group.add(explainer)
    expander = Gtk.Expander(label="Afficher les capacités par source")
    expander.set_expanded(False)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
    if labels:
        for value in labels:
            row = Gtk.Label(label=value, xalign=0)
            row.set_wrap(True)
            row.set_selectable(True)
            box.append(row)
    else:
        row = Gtk.Label(
            label="Aucune source qualifiée pour l’identité sélectionnée.",
            xalign=0,
        )
        row.set_wrap(True)
        box.append(row)
    expander.set_child(box)
    group.add(expander)
    return True


__all__ = ["source_capability_labels", "append_settings_capability_overview"]
