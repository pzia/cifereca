"""P0 #1012: formatter must be readable, redacted and type-preserving."""
import json
import pytest

from cifereca_diagnostics_page import _safe_text, redacted_detail, format_diagnostic_detail


def test_structured_mapping_uses_indented_lines_not_python_repr():
    detail = {"checks": [{"module": "kobo", "ok": False}], "n": None, "empty": "", "zero": 0}
    shown = format_diagnostic_detail(detail)
    assert "checks:\n  –\n    module: \"kobo\"" in shown
    assert "ok: false" in shown
    assert "n: null" in shown
    assert 'empty: ""' in shown
    assert "zero: 0" in shown
    assert "{'checks':" not in shown


def test_json_mode_has_real_types_stable_order_and_unicode():
    original = {"école": "L'Été", "zero": 0, "none": None, "off": False, "rows": [1, 2]}
    shown = format_diagnostic_detail(original, mode="json")
    assert json.loads(shown) == original
    assert shown.startswith("{\n")
    assert "Été" in shown
    assert list(json.loads(shown)) == sorted(original, key=str.casefold)


def test_plain_non_json_strings_are_not_reparsed():
    sample = "{not: JSON}\n   abc"
    assert format_diagnostic_detail(sample, "readable") == sample
    assert format_diagnostic_detail(sample, "raw") == sample
    assert json.loads(format_diagnostic_detail(sample, "json")) == sample


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_all_modes_pseudonymize_only_password_preserving_all_other_fields(mode):
    data = {
        "password": "TOP-SECRET-123",
        "credential_id": "CARD-SECRET-456",
        "message": "Bearer BEARER-SECRET token=abc123 alice@example.com /home/alex/test.epub",
        "url": "https://server.example/LOAN/abc?token=123",
        "status": "partial",
    }
    shown = format_diagnostic_detail(data, mode)
    assert "TOP-SECRET-123" not in shown
    assert "mot-de-passe-" in shown
    for value in ("CARD-SECRET", "BEARER-SECRET", "abc123", "alice@example.com", "/home/alex", "/LOAN/abc"):
        assert value in shown
    if mode == "json":
        assert json.loads(shown)["status"] == "partial"


def test_cycles_unsupported_values_and_nan_become_safe_markers():
    class Bad:
        def __repr__(self):
            raise AssertionError("dangerous repr")
        def __str__(self):
            raise AssertionError("dangerous str")
    cycle = []
    cycle.append(cycle)
    val = json.loads(format_diagnostic_detail({
        "cycle": cycle, "bad": Bad(), "nan": float("nan"),
    }, "json"))
    assert val["cycle"] == ["<référence circulaire>"]
    assert val["bad"] == "<type non pris en charge>"
    assert val["nan"] == "<nombre non fini>"


def test_nested_output_is_bounded_even_on_extreme_inputs():
    data = {"array": ["x" * 800 for _ in range(200)]}
    raw_json = format_diagnostic_detail(data, "json")
    assert len(raw_json) < 24100
    assert json.loads(raw_json)
    assert "tronqu" in raw_json.lower() or "limité" in raw_json.lower()


def test_null_false_zero_and_empty_containers_remain_distinct():
    assert _safe_text(None) == "null"
    assert _safe_text(False) == "false"
    assert _safe_text(0) == "0"
    assert _safe_text("") == ""
    assert _safe_text([]) == "[]"
    assert _safe_text({}) == "{}"


def test_unknown_mode_refused():
    with pytest.raises(ValueError):
        format_diagnostic_detail({"x": 1}, "unsafe")


def test_keys_and_values_are_preserved_except_explicit_credentials():
    detail = {
        "alice@example.com": "first",
        "bob@example.com": "second",
        "token": "never reveal this",
    }
    safe = redacted_detail(detail)
    assert len(safe) == 3
    assert sorted(safe.values()) == ["first", "never reveal this", "second"]
    assert len(set(safe)) == 3
    shown = format_diagnostic_detail(detail, mode="json")
    assert len(json.loads(shown)) == 3
    assert "alice@example.com" in shown and "bob@example.com" in shown
    assert "never reveal this" in shown


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_authentication_headers_preserved_except_username_field(mode):
    details = {
        "message": (
            "Authorization: Basic dXNlcjpzZWNyZXQ=\\n"
            "Proxy-Authorization: Digest username=alice, realm=private, response=abc123\\n"
            "X-API-Key: KEY-PRIVATE-123\\nstatus=ok"
        ),
        "status": "ok",
    }
    shown = format_diagnostic_detail(details, mode)
    assert "username=alice" not in shown
    assert "username=login-" in shown
    for text in ("dXNlcjpzZWNyZXQ=", "realm=private", "response=abc123", "KEY-PRIVATE-123"):
        assert text in shown
    assert "status" in shown


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_only_password_is_masked_in_quoted_text(mode):
    data = {"message": 'token="mot de passe privé" cookie=\'session longue privée\' password=ordinary'}
    shown = format_diagnostic_detail(data, mode)
    assert "mot de passe privé" in shown
    assert "session longue privée" in shown
    assert "ordinary" not in shown
    assert "password=mot-de-passe-" in shown


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_non_login_keys_remain_visible(mode):
    detail = {"api_key": "API-PRIVATE-ABC", "access_key": "ACCESS-PRIVATE-XYZ",
              "api-key": "SECOND-API-SECRET", "status": "ok"}
    shown = format_diagnostic_detail(detail, mode)
    for text in ("API-PRIVATE-ABC", "ACCESS-PRIVATE-XYZ", "SECOND-API-SECRET"):
        assert text in shown
    assert "ok" in shown
