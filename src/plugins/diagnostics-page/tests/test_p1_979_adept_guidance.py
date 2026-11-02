"""P1 #979 — closed, contextualized ADEPT advice in diagnostics journal.

Synthetic v1 broker events only; no executable, network, private profile,
automatic retry, account mutation or artifact access is performed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from diagnostics_adept_guidance import adept_guidance_for_event

PRIVATE = "PRIVATE_ID https://private.invalid/?token=PRIVATE_ID"


def event(code, *, source="core_events", **changes):
    detail = {
        "schema_version": 1,
        "plugin_id": "runtime.kobo-adept",
        "operation_kind": "kobo_adept",
        "operation": "download request",
        "phase": "completed",
        "status": "failed",
        "code": code,
        "returncode": 10,
        "retryable": False,
    }
    detail.update(changes)
    return {
        "source": source,
        "module": "ADEPT",
        "message": PRIVATE,
        "detail": detail,
    }


@pytest.mark.parametrize(("code", "needle"), [
    ("invalid_acsm", "Jeton ACSM signalé invalide"),
    ("profile_not_found", "Profil ADEPT introuvable"),
    ("adept_profile_required", "Profil ADEPT requis"),
    ("adept_profile_error", "Erreur de profil ADEPT"),
    ("adept_profile_incompatible", "Profil ADEPT incompatible"),
    ("adept_license_already_fulfilled_by_another_user",
     "une autre identité Adobe"),
    ("adept_error", "cause Adobe précise n'est pas attestée"),
    ("kobo_adept_timeout", "peut avoir consommé le jeton"),
    ("kobo_adept_missing", "Exécutable Kobo-ADEPT introuvable"),
    ("kobo_adept_invalid_response", "Réponse Kobo-ADEPT incohérente"),
])
@pytest.mark.parametrize("source", ["core_events", "app_log", "journal"])
def test_attested_closed_error_gives_fixed_guidance_only(code, needle, source):
    row = event(code, source=source, stderr=PRIVATE,
                technical_detail=PRIVATE)
    # Unknown extra fields must be rejected; strip them to match the
    # exact Core-public diagnostic envelope.
    del row["detail"]["stderr"]
    del row["detail"]["technical_detail"]
    label = adept_guidance_for_event(row)
    assert label.startswith("ADEPT · ")
    assert needle in label and "Code : " + code in label
    assert PRIVATE not in label
    assert "rejou" not in label.lower()
    assert "automatiquement l'activation" not in label or (
        code == "adept_license_already_fulfilled_by_another_user"
    )
    assert row["detail"]["returncode"] == 10  # never a cause by itself


@pytest.mark.parametrize("bad", [
    event(PRIVATE),
    event("E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER"),
    event("adept_error", schema_version=2),
    event("adept_error", plugin_id="runtime.other"),
    event("adept_error", operation_kind="other"),
    event("adept_error", phase="running"),
    event("adept_error", status="success"),
    event("adept_error", retryable=1),
    event("adept_error", stderr=PRIVATE),
    event("adept_error", operation="acsm return"),
    event("adept_error", operation=PRIVATE),
    event("adept_error", status=PRIVATE),
    event("adept_error", source="unknown"),  # wrong nesting, no authorization
    {"source": "core_events", "message": PRIVATE, "detail": {"code": "adept_error"}},
    {"source": "core_events", "message": PRIVATE, "detail": {"returncode": 10}},
    {"source": "core_events", "message": PRIVATE, "detail": PRIVATE},
    {"source": "core_events", "message": PRIVATE, "detail": {}},
    {"source": "core_events", "message": PRIVATE, "detail": []},
    {"source": "core_events", "message": PRIVATE, "detail": None},
    None,
])
def test_unknown_malformed_or_unqualified_events_are_not_given_a_license_cause(bad):
    assert adept_guidance_for_event(bad) == ""



@pytest.mark.parametrize("code", [
    "invalid_acsm", "adept_license_already_fulfilled_by_another_user",
    "kobo_adept_timeout",
])
@pytest.mark.parametrize("operation", [
    "profile list", "loans list", "capabilities", "status",
])
def test_acsmspecific_cause_requires_download_request_operation(code, operation):
    assert adept_guidance_for_event(event(code, operation=operation)) == ""


def test_retries_remain_unadvised_even_when_native_retryable_flag_is_true():
    label = adept_guidance_for_event(
        event("invalid_acsm", retryable=True)
    )
    assert "Jeton ACSM signalé invalide" in label
    assert "Réessayez" not in label
    assert "Répéter" not in label


def test_only_code_drives_fixed_output_never_private_message_or_exit_code():
    clean = adept_guidance_for_event(event("adept_error"))
    hostile = event("adept_error")
    hostile["message"] = PRIVATE
    hostile["detail"]["returncode"] = 255
    assert adept_guidance_for_event(hostile) == clean
    assert PRIVATE not in clean
    no_code = event("adept_error")
    del no_code["detail"]["code"]
    assert adept_guidance_for_event(no_code) == ""


def test_sanitized_broker_event_keeps_structured_code_for_guidance_only():
    from diagnostics_explorer import sanitized_rows

    row = event("adept_profile_incompatible")
    row.update({
        "timestamp": "2026-10-30T12:00:00+00:00",
        "severity": "error",
        "event_id": "synthetic-event",
        "status": "failed",
    })
    projected = sanitized_rows({"events": [row]}, include_detail=True)
    assert len(projected) == 1
    assert "Profil ADEPT incompatible" in adept_guidance_for_event(projected[0])
    # A selection/export without detail must not capture the new suggestion
    # as false provider evidence or a new error code.
    public = sanitized_rows({"events": [row]}, include_detail=False)
    assert "detail" not in public[0]
    assert adept_guidance_for_event(public[0]) == ""


def test_journal_gtk_renderer_displays_closed_guidance_without_new_actions():
    root = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"
    tree = ast.parse(root.read_text(encoding="utf-8"))
    plugin = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                  and n.name == "DiagnosticsPagePlugin")
    build = next(n for n in plugin.body if isinstance(n, ast.FunctionDef)
                 and n.name == "_build")
    body = ast.unparse(build)
    assert "adept_advice = adept_guidance_for_event(row)" in body
    assert "next_step = Gtk.Label(label=adept_advice, xalign=0)" in body
    assert "item.append(next_step)" in body
    assert "runner.submit" not in body
