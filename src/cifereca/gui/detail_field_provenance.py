"""Render the existing per-field provenance without inferring authority."""
from __future__ import annotations

from cifereca.book_detail_metadata import safe_detail_value
from cifereca.plugin_api.models import Provenance


def detail_field_provenance(field) -> str:
    proof = getattr(field, "provenance", None)
    if type(proof) is not Provenance:
        return ""
    lines = []
    for label, value in (
        ("Source du champ", proof.source),
        ("Fournisseur déclaré", proof.provider_id),
        ("Référence déclarée", proof.source_id),
        ("Observation déclarée", proof.observed_at),
    ):
        # Reuse the metadata text boundary; never stringify arbitrary source
        # objects or traverse free-form provenance.metadata at render time.
        text = safe_detail_value(value) if type(value) is str else ""
        if text:
            lines.append(f"{label} : {text}")
    return "\n".join(lines) or "Provenance non renseignée"


def annotate_detail_field(row, label, value, field, *, description_property=None):
    """Keep the complete value and declared source together for each row.

    Source labels and dates remain declarations, never an 'attested' status,
    freshness verdict or entitlement. Missing legacy provenance stays absent.
    """
    tooltip = f"{label} : {value}"
    provenance = detail_field_provenance(field)
    if provenance:
        tooltip += "\n" + provenance
    row.set_tooltip_text(tooltip)
    if provenance and description_property is not None:
        row.update_property([description_property], [tooltip])
