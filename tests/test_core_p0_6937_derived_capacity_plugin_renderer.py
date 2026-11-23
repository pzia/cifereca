"""#6937: capacity refusal text must not become a false quota grant."""
from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from cifereca.plugin_api.derived_maintenance import DerivedCapacityObservation


def _render():
    path = Path(__file__).resolve().parents[1] / (
        "src/plugins/maintenance-derived-variants/cifereca_derived_maintenance.py"
    )
    spec = spec_from_file_location("capacity_p0_renderer", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.capacity_text


def _sample():
    return DerivedCapacityObservation(
        status="observed",
        counts=tuple((k, n) for k, n in (
            ("pending", 1), ("committed", 1), ("unreadable", 0),
            ("unbound", 0), ("active_pending", 1),
            ("active_committed", 1), ("other_bound", 0),
        )),
        record_count=2, payload_bytes=512, known_output_bytes=4096,
        output_bytes_complete=True, active_payload_bytes=512,
        active_output_bytes=4096,
        capacity=(("records", 1024), ("payload_bytes", 8388608),
                  ("output_bytes", 17179869184)),
        saturation="below_limits",
        stock_status="partial", logical_bytes=4096, allocated_bytes=8192,
        region_status="observed",
        region_counts=(("pending_named", 1), ("committed_named", 1),
                       ("unrecorded_regular", 0), ("ambiguous_entries", 0),
                       ("unverified_regions", 0), ("unobserved_receipts", 0)),
        compact_status="observed",
        compact_counts=(("count", 1), ("payload_bytes", 128),
                        ("matched", 1), ("unpaired", 0), ("retired", 0)),
        compact_capacity=(("records", 1024), ("payload_bytes", 8388608)),
        compact_saturation="below_limits",
    )


@pytest.mark.parametrize("change", [
    {"counts": (("pending", 1), ("pending", 1))},
    {"capacity": (("records", 1024), ("payload_bytes", 8388608))},
    {"compact_capacity": (("records", 1024), ("records", 1024))},
    {"region_counts": (("pending_named", 1),)},
    {"compact_counts": (("count", 1), ("payload_bytes", 128),
                        ("matched", 0), ("unpaired", 0), ("retired", 1))},
    {"saturation": "below_limit_typo"},
    {"compact_saturation": "below_limit_typo"},
    {"output_bytes_complete": 1},
    {"record_count": -1},
    {"known_output_bytes": True},
    {"logical_bytes": -1},
    {"allocated_bytes": None},
])
def test_malformed_capacity_cannot_be_rendered_as_free_quota(change):
    view = _render()(replace(_sample(), **change))
    assert view == "Capacité indisponible. Aucun espace libre attesté."
    assert "Sous les plafonds" not in view
    assert "quota libéré" not in view


def test_valid_capacity_displays_both_limits_but_no_right_to_allocate():
    text = _render()(_sample())
    assert "2/1024 reçus" in text
    assert "Sous les plafonds observés (pas une réservation)" in text
    assert "Traces compactes" in text
    assert "Aucun quota libéré" in text
