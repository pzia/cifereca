import pytest

from diagnostics_explorer import empty_observation, sanitized_rows


def payload(*statuses):
    return {"total": 0, "sources": [{"source": f"source-{i}", "status": status}
                                      for i, status in enumerate(statuses)]}


def test_empty_available_is_not_missing_or_a_health_guarantee():
    result = empty_observation(payload("available", "empty"), period="day")
    assert "Pas d’erreur observée" in result and "24 heures" in result
    assert "ne garantit pas" in result
    assert "Aucune donnée exploitable" in empty_observation(payload(), period="day")


@pytest.mark.parametrize("status", ["partial", "unavailable", "disabled"])
def test_incomplete_coverage_never_claims_all_sources_clear(status):
    result = empty_observation(payload("available", status), period="week")
    assert "Couverture partielle" in result and "7 jours" in result
    assert "Pas d’erreur observée" not in result
    assert "Aucune anomalie" not in result
    assert "Aucun événement publié dans l’extrait consultable" in result
    assert "journaux tronqués" in result


@pytest.mark.parametrize("filter", ["severity", "module", "search"])
def test_filtered_empty_is_not_an_inference_about_all_errors(filter):
    result = empty_observation(payload("available"), period="day", **{filter: "info"})
    assert "correspondant aux filtres" in result
    assert "Pas d’erreur" not in result


@pytest.mark.parametrize("filter", ["severity", "module", "search"])
def test_filtered_partial_evidence_never_claims_exhaustive_coverage(filter):
    result = empty_observation(
        payload("available", "partial"), period="day", **{filter: "sync"}
    )
    assert "correspondant aux filtres" in result
    assert "Couverture partielle" in result
    assert "événements manquants possibles" in result
    assert "Pas d’erreur observée" not in result


def test_global_partial_status_must_be_disclosed_even_with_sources_available():
    rows = payload("available", "empty")
    rows["status"] = "partial"
    assert "Couverture partielle" in empty_observation(rows, period="week")
    assert "Pas d’erreur observée" not in empty_observation(rows, period="week")
    rows["status"] = "available"
    assert "Pas d’erreur observée" in empty_observation(rows, period="week")


def test_filter_does_not_invent_absence_when_all_sources_missing():
    result = empty_observation(payload("unavailable", "disabled"),
                               period="day", module="kobo")
    assert result == "Aucune donnée exploitable dans les sources consultées."


def test_event_detail_is_safe_and_opt_in_so_ai_does_not_inherit_it():
    raw = {"event_id": "evt-1", "detail": {
        "reason": "provider_timeout", "token": "PRIVATE", "person": "PRIVATE_NAME"}}
    source = {"events": [raw]}
    assert "detail" not in sanitized_rows(source)[0]
    detail = sanitized_rows(source, include_detail=True)[0]["detail"]
    assert detail["reason"] == "provider_timeout" and detail["token"] == "PRIVATE"
    assert raw["detail"]["token"] == "PRIVATE"  # Input is not mutated.
