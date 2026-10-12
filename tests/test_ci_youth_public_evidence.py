"""No-network publication contracts: malicious URLs, errors, titles and paths (#1144)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "public_youth_evidence", ROOT / "scripts/youth_evidence_public.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
SOURCES = {
    "milan": {"host": "www.editionsmilan.com", "plugin": "youth.source.milan"},
    "ecole-loisirs": {"host": "www.ecoledesloisirs.fr", "plugin": "youth.source.ecole-loisirs"},
}


def test_pass_export_is_allowlisted_and_never_contains_publication_secrets():
    poison = "https://private.example/ebook.acsm?access_token=SECRET&cookie=SECRET"
    report = {
        "source": "milan", "result": "PASS", "age": 8, "limit": 2,
        "request_budget": 9, "requests_count": 1,
        "https_requests": [poison], "public_query": "PRIVATE QUERY",
        "qualified_notices": [{"title": "PRIVATE BOOK", "isbn13": "PRIVATE ISBN",
                               "notice_url": poison, "published_label": "PRIVATE AGE"}],
        "ingest_errors": ["SECRET Authorization: Bearer hi", "accounts PRIVATE"],
        "accepted": 1, "rejected": 0, "next_cursor_present": True,
        "error_type": "RuntimeError", "reason": "PRIVATE /home/user/account.json",
        "source_commit": "f" * 40, "core_version": "1.2.3",
        "plugin_diagnostic": {"source": "milan", "http_status": 200,
                              "anchor_count": 5, "html_bytes": 250,
                              "html": "PRIVATE HTML", "url": poison,
                              "error_type": "RuntimeError", "headers": {"Cookie": "SECRET"}},
    }
    result = mod.public_report(report, SOURCES)
    serialized = json.dumps(result, ensure_ascii=False)
    for forbidden in ("PRIVATE", "SECRET", "https://", "Bearer", "account.json",
                      'qualified_notices"', "https_requests", "ingest_errors", 'reason"'):
        assert forbidden not in serialized, forbidden
    assert result["qualified_notices_count"] == 1
    assert result["ingest_error_count"] == 2
    assert result["error_code"] == "QUALIFICATION_RUNTIME"
    assert result["plugin_diagnostic"]["http_status"] == 200
    assert result["plugin_diagnostic"]["error_code"] == "QUALIFICATION_RUNTIME"


def test_failure_error_and_url_payloads_are_never_serialized():
    payloads = ("https://private.example/?sig=SECRET", "Cookie: SECRET",
                "Authorization: SECRET", "C:/Users/A/private.acsm",
                "/home/a/.config/adept/activation.dat", "user@example.com")
    for raw in payloads:
        report = {"source": "ecole-loisirs", "result": "BLOCKED",
                  "reason": raw, "error_type": raw, "ingest_errors": [raw],
                  "https_requests": [raw], "requests_count": 1,
                  "plugin_diagnostic": {"source": "ecole-loisirs", "error_type": raw,
                                        "state": "error", "page": 2,
                                        "headers": raw}}
        pub = mod.public_report(report, SOURCES)
        assert raw not in json.dumps(pub)
        assert pub["error_code"] == "OTHER_ERROR"
        assert pub["reason_code"] == "QUALIFICATION_BLOCKED"
        assert pub["ingest_error_count"] == 1


def test_network_without_any_request_is_not_mislabelled_clear():
    pub = mod.public_report({"source": "milan", "result": "BLOCKED",
                             "requests_count": 0}, SOURCES)
    assert pub["reason_code"] == "NO_HTTPS_REQUEST"


def test_invalid_source_bogus_status_and_bad_types_fail_closed():
    assert mod.public_report({"source": "user@example.com", "result": "PASS"}, SOURCES) == {
        "result": "BLOCKED", "reason_code": "INVALID_SOURCE"
    }
    result = mod.public_report({"source": "milan", "result": [],
                                "accepted": True,
                                "plugin_diagnostic": {"source": "milan", "phase": [],
                                                      "state": ["error"],
                                                      "http_status": "200"}}, SOURCES)
    assert result["result"] == "BLOCKED"
    assert "accepted" not in result
    assert result["plugin_diagnostic"] == {"source": "milan"}


def test_opt_in_is_not_allowed_to_leak_any_free_form_reason():
    pub = mod.public_report({"source": "milan", "result": "NOT_RUN",
                             "reason": "Authorization: BEARER", "source_commit": "../token"}, SOURCES)
    assert pub["reason_code"] == "CONFIRM_LIVE_REQUIRED"
    assert "source_commit" not in pub


def test_http_status_and_digests_are_checked():
    pub = mod.public_report({"source": "milan", "result": "NO_VERIFIED_NOTICE",
                             "plugin_diagnostic": {"source": "milan", "http_status": 200,
                                                   "html_sha256_prefix": "b" * 16,
                                                   "html_bytes": 2**64,
                                                   "next_cursor_present": 1}}, SOURCES)
    assert pub["plugin_diagnostic"] == {"source": "milan", "http_status": 200,
                                         "html_sha256_prefix": "b" * 16}
