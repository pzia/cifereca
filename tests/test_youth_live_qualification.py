"""No-network regressions for the explicitly opt-in youth HTTPS check."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS


def script_module():
    path = Path(__file__).resolve().parents[1] / "scripts/qualify-youth-source-live.py"
    spec = importlib.util.spec_from_file_location("live_youth_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_never_imports_core_or_attempts_live_https(capsys):
    script = script_module()
    assert script.main(["--source", "milan"]) == 2
    assert '"result": "NOT_RUN"' in capsys.readouterr().out


def test_input_limits_and_hard_request_budget(capsys):
    script = script_module()
    for argv in (
        ["--source", "milan", "--age", "18", "--confirm-live"],
        ["--source", "milan", "--age", "-1", "--confirm-live"],
        ["--source", "milan", "--limit", "4", "--confirm-live"],
        ["--source", "milan", "--timeout", "30", "--confirm-live"],
    ):
        assert script.main(argv) == 2
    assert "INVALID_INPUT" in capsys.readouterr().out
    assert script.plan(script.parser().parse_args(["--source", "milan"]))["request_budget"] == 9


def test_evidence_requires_real_source_label_and_origin():
    script = script_module()
    age = NS(source_id="milan", age_kind="publisher", source_label="À partir de 8 ans",
             age_min=8, age_max=None)
    book = dict(title="Lumilane", identifiers={"isbn": "9782408060381"},
                age_ranges=(age,), notice_url="https://www.editionsmilan.com/livres/97384-lumilane/",
                provenance=("milan",))
    assert len(script.evidence_for((NS(**book),), "milan")) == 1
    assert not script.evidence_for((NS(**(book | {"provenance": ()})),), "milan")
    assert not script.evidence_for((NS(**(book | {"provenance": ("openlibrary",)})),), "milan")
    assert not script.evidence_for((NS(**(book | {"notice_url": "https://evil.example/"})),), "milan")
    assert not script.evidence_for((NS(**(book | {"identifiers": {}})),), "milan")
    wrong = NS(**(vars(age) | {"source_label": ""}))
    assert not script.evidence_for((NS(**(book | {"age_ranges": (wrong,)})),), "milan")


def test_live_qualification_requires_an_observed_https_request():
    script = script_module()
    qualified = [{"title": "Example", "provenance": ["milan"]}]
    assert script.qualification_status([], qualified) == "BLOCKED"
    assert script.qualification_status(["https://www.editionsmilan.com/livres/"], []) == "NO_VERIFIED_NOTICE"
    assert script.qualification_status(["https://www.editionsmilan.com/livres/"], qualified) == "PASS"


def test_milan_diagnostic_export_whitelists_bounded_fields_and_redacts_raw_data():
    script = script_module()
    diagnostic = {
        "source": "milan", "http_status": 200, "html_bytes": 24889,
        "html_sha256_prefix": "a" * 16, "phase": "products",
        "product_href_count": 3, "anchor_count": 12,
        "next_cursor_present": True, "detail_requests": 2,
        "error_type": "RuntimeError",
        "title": "PRIVATE BOOK", "html": "<div>secret</div>",
        "url": "https://www.editionsmilan.com/livres/secret?token=abc",
        "http_headers": {"Authorization": "secret"},
        "cookies": ["private"], "bad_counter": 4,
        "age_filter_present": 1,  # int is not the expected boolean type
        "qualified_items": -5,
    }
    source = NS(diagnostic_snapshot=lambda: diagnostic)
    safe = script.plugin_diagnostic_for(source, "milan")
    assert safe == {
        "source": "milan", "http_status": 200, "html_bytes": 24889,
        "html_sha256_prefix": "a" * 16, "phase": "products",
        "product_href_count": 3, "anchor_count": 12,
        "next_cursor_present": True, "detail_requests": 2,
        "error_type": "RuntimeError",
    }
    assert script.plugin_diagnostic_for(source, "openlibrary") == {}
    assert script.plugin_diagnostic_for(NS(), "milan") == {}
    assert script.plugin_diagnostic_for(
        NS(diagnostic_snapshot=lambda: 1 / 0), "milan",
    ) == {}


def test_milan_diagnostic_export_rejects_unstructured_or_unbounded_values():
    script = script_module()
    source = NS(diagnostic_snapshot=lambda: {
        "source": "milan",
        "phase": ["products"],
        "http_status": True,
        "html_bytes": 10**12,
        "html_sha256_prefix": "not a digest",
        "error_type": "HTTPError: private https://example.com",
        "anchor_count": 8,
    })
    assert script.plugin_diagnostic_for(source, "milan") == {
        "source": "milan", "anchor_count": 8,
    }


def test_failed_live_qualification_preserves_structural_plugin_diagnostic(
    tmp_path, monkeypatch,
):
    """One fake request via event hook; absolutely no outgoing network."""
    import pytest
    import httpx
    from cifereca.core import CiferecaCore

    script = script_module()
    class FakeSource:
        def __init__(self):
            self.client = NS(close=lambda: None)

        def diagnostic_snapshot(self):
            return {
                "source": "milan", "phase": "listing",
                "http_status": 200, "anchor_count": 31,
                "product_href_count": 0, "next_cursor_present": True,
                "html_sha256_prefix": "a" * 16,
                "html": "<body>should never be exported</body>",
            }

        def browse(self, **kwargs):
            req = httpx.Request("GET", "https://www.editionsmilan.com/livres/")
            self.client.event_hooks["request"][0](req)
            raise RuntimeError("Milan paginated catalogue has no recognizable book links")

    source = FakeSource()
    class FakePlugins:
        def install(self, _archive):
            return None

        def service(self, plugin_id):
            if plugin_id == "catalog.youth":
                return NS()
            if plugin_id == "youth.source.milan":
                return source
            return None

    monkeypatch.setattr(CiferecaCore, "open", lambda **kwargs: NS(plugins=FakePlugins()))
    monkeypatch.setattr(script, "bundle", lambda *_: tmp_path / "fake.zip")
    monkeypatch.setattr(script.os, "environ", dict(script.os.environ))
    args = script.parser().parse_args(["--source", "milan", "--confirm-live"])
    report = {}
    with pytest.raises(RuntimeError, match="no recognizable book links"):
        script.qualify(args, report)
    assert report["requests_count"] == 1
    assert "https_requests" not in report
    assert "https://www.editionsmilan.com/livres/" not in json.dumps(report)
    assert report["plugin_diagnostic"] == {
        "source": "milan", "phase": "listing",
        "http_status": 200, "anchor_count": 31,
        "product_href_count": 0, "next_cursor_present": True,
        "html_sha256_prefix": "a" * 16,
    }



def test_ecole_loisirs_live_qualification_is_opt_in_and_single_request(capsys):
    script = script_module()
    args = script.parser().parse_args(["--source", "ecole-loisirs", "--age", "3"])
    plan = script.plan(args)
    assert plan["request_budget"] == 1
    assert plan["allowed_host"] == "www.ecoledesloisirs.fr"
    assert script.main(["--source", "ecole-loisirs"]) == 2
    assert '"result": "NOT_RUN"' in capsys.readouterr().out
    assert script.SOURCES["ecole-loisirs"]["plugin"] == "youth.source.ecole-loisirs"


def test_ecole_loisirs_evidence_needs_attested_age_valid_isbn_and_public_origin():
    script = script_module()
    source_id = "ecole-loisirs"
    age = NS(source_id=source_id, age_kind="publisher",
             source_label="3 à 6 ans", age_min=3, age_max=6)
    row = dict(
        title="Milo et le loup", identifiers={"isbn": "9782211309684"},
        age_ranges=(age,),
        notice_url="https://www.ecoledesloisirs.fr/livre/milo-loup",
        provenance=(source_id,),
    )
    assert len(script.evidence_for((NS(**row),), source_id)) == 1
    assert not script.evidence_for((NS(**(row | {"identifiers": {}})),), source_id)
    assert not script.evidence_for((NS(**(row | {"provenance": ()})),), source_id)
    assert not script.evidence_for((NS(**(row | {"notice_url": "https://evil.example/livre/milo-loup"})),), source_id)
    assert not script.evidence_for((NS(**(row | {"age_ranges": ()})),), source_id)


def test_ecole_loisirs_routes_only_one_explicit_search_page_not_browse():
    source = (Path(__file__).resolve().parents[1] /
              "scripts/qualify-youth-source-live.py").read_text(encoding="utf-8")
    assert 'raw = source.search("loup", cursor=None, limit=40)' in source
    assert '"age": args.age' in source  # local cache filter, never HTTP age parameter
    assert 'report["public_query"]' not in source
    assert '"max_requests": 1' in source



def test_ecole_diagnostic_export_whitelists_fields_and_redacts_private_values():
    script = script_module()
    source = NS(diagnostic_snapshot=lambda: {
        "source": "ecole-loisirs", "phase": "complete", "state": "items",
        "http_status": 200, "request_count": 1, "html_bytes": 19832,
        "page": 1, "requested_limit": 40, "product_url_count": 8,
        "anchor_count": 29, "returned_items": 5,
        "qualified_age_count": 4, "unqualified_age_count": 1,
        "duration_ms": 132, "text_filter_present": True,
        "content_type_is_html": True, "next_cursor_present": False,
        "html_sha256_prefix": "f" * 16,
        # Not part of the diagnostic contract: do not leak any of these.
        "raw_html": "<html>private</html>", "query": "a private query",
        "title": "PRIVATE", "url": "https://www.ecoledesloisirs.fr/?token=secret",
        "headers": {"Cookie": "secret"}, "cookies": ["private"],
    })
    report = script.plugin_diagnostic_for(source, "ecole-loisirs")
    assert report == {
        "source": "ecole-loisirs", "phase": "complete", "state": "items",
        "http_status": 200, "request_count": 1, "html_bytes": 19832,
        "page": 1, "requested_limit": 40, "product_url_count": 8,
        "anchor_count": 29, "returned_items": 5,
        "qualified_age_count": 4, "unqualified_age_count": 1,
        "duration_ms": 132, "text_filter_present": True,
        "content_type_is_html": True, "next_cursor_present": False,
        "html_sha256_prefix": "f" * 16,
    }
    assert script.plugin_diagnostic_for(source, "milan") == {}
    assert script.plugin_diagnostic_for(NS(), "ecole-loisirs") == {}


def test_ecole_diagnostic_export_rejects_malformed_and_unbounded_data():
    script = script_module()
    source = NS(diagnostic_snapshot=lambda: {
        "source": "ecole-loisirs", "phase": "download",
        "state": "unverified", "page": True,
        "html_bytes": -3, "request_count": 10**20,
        "duration_ms": "secret", "text_filter_present": 1,
        "html_sha256_prefix": "NOT_A_HASH",
        "error_type": "RuntimeError: private https://example.com",
        "qualified_age_count": 2,
    })
    assert script.plugin_diagnostic_for(source, "ecole-loisirs") == {
        "source": "ecole-loisirs", "qualified_age_count": 2,
    }
    assert script.plugin_diagnostic_for(
        NS(diagnostic_snapshot=lambda: 1 / 0), "ecole-loisirs",
    ) == {}


def test_failed_ecole_qualification_retains_safe_snapshot_without_extra_http(
    tmp_path, monkeypatch,
):
    import httpx
    import pytest
    from cifereca.core import CiferecaCore

    script = script_module()

    class FakeSource:
        def __init__(self):
            self.client = NS(close=lambda: None)
            self.calls = 0

        def diagnostic_snapshot(self):
            return {
                "source": "ecole-loisirs", "state": "error",
                "phase": "listing", "http_status": 503,
                "request_count": 1, "html_bytes": 1970,
                "error_type": "RuntimeError",
                "html": "<html>must never be returned</html>",
            }

        def search(self, query, *, cursor=None, limit=40):
            assert query == "loup" and cursor is None and limit == 40
            self.calls += 1
            self.client.event_hooks["request"][0](
                httpx.Request(
                    "GET",
                    "https://www.ecoledesloisirs.fr/recherche-avancee"
                    "?page=1&search_api_views_fulltext=loup&types=livre",
                )
            )
            raise RuntimeError("l'école des loisirs HTTP 503")

    source = FakeSource()

    class FakePlugins:
        def install(self, _archive):
            return None

        def service(self, plugin_id):
            if plugin_id == "catalog.youth":
                return NS()
            if plugin_id == "youth.source.ecole-loisirs":
                return source
            return None

    monkeypatch.setattr(CiferecaCore, "open", lambda **kwargs: NS(plugins=FakePlugins()))
    monkeypatch.setattr(script, "bundle", lambda *_: tmp_path / "fake.zip")
    monkeypatch.setattr(script.os, "environ", dict(script.os.environ))
    args = script.parser().parse_args(["--source", "ecole-loisirs", "--confirm-live"])
    report = {}
    with pytest.raises(RuntimeError, match="HTTP 503"):
        script.qualify(args, report)
    assert source.calls == 1
    assert report["requests_count"] == 1
    assert report["plugin_diagnostic"] == {
        "source": "ecole-loisirs", "state": "error",
        "phase": "listing", "http_status": 503,
        "request_count": 1, "html_bytes": 1970,
        "error_type": "RuntimeError",
    }
    assert "<html>" not in str(report)



def test_live_evidence_rejects_valid_ean_check_digits_without_isbn_prefix():
    """No live network: only exact 978/979 ISBN-13 with a valid check digit."""
    script = script_module()
    assert script._valid_isbn13("9782211309684")
    assert script._valid_isbn13("9791234567896")
    for bad in (
        "3575678940134",  # toy/peluche EAN-13, valid GTIN check digit
        "3127020507597",  # jeu EAN-13, valid GTIN check digit
        "9780000000000",  # ISBN prefix but invalid ISBN checksum
        "978221130968",   # missing digit
        "978221130968X",  # invalid ISBN-13 character
        "9782211309684\n", # no extraneous whitespace
    ):
        assert not script._valid_isbn13(bad)

    for source_id, host in (
        ("ecole-loisirs", "www.ecoledesloisirs.fr"),
        ("milan", "www.editionsmilan.com"),
    ):
        age = NS(source_id=source_id, age_kind="publisher",
                 source_label="3 à 6 ans", age_min=3, age_max=6)
        base = dict(title="Livre", age_ranges=(age,),
                    provenance=(source_id,),
                    notice_url=f"https://{host}/livre/ouvrage")
        for bad in ("3575678940134", "3127020507597", "9780000000000"):
            row = NS(**(base | {"identifiers": {"isbn": bad}}))
            assert script.evidence_for((row,), source_id) == []
        for good in ("9782211309684", "9791234567896"):
            row = NS(**(base | {"identifiers": {"isbn": good}}))
            actual = script.evidence_for((row,), source_id)
            assert len(actual) == 1 and actual[0]["isbn13"] == good


def test_published_evidence_is_fail_closed_even_for_poisoned_report():
    script = script_module()
    private = "Bearer PRIVATE_TOKEN /home/alice/book.acsm ?token=SECRET"
    raw = {
        "source": "milan", "result": "PASS", "requests_count": 2,
        "accepted": 1, "rejected": 2, "ingest_error_count": 1,
        "qualified_notice_count": 1, "next_cursor_present": False,
        "age": 8, "limit": 2, "request_budget": 9, "timeout_seconds": 6,
        "source_plugin_id": "youth.source.milan",
        "https_requests": ["https://www.editionsmilan.com/private?token=SECRET"],
        "qualified_notices": [{"title": private, "isbn13": "9782211309684"}],
        "ingest_errors": [private], "reason": private,
        "error_type": private, "public_query": private,
        "plugin_diagnostic": {
            "source": "milan", "http_status": 200,
            "error_type": "PRIVATE_ACCESS_TOKEN",
            "raw_html": private, "url": private, "cookie": private,
        },
    }
    safe = script.public_report(raw)
    assert safe["result"] == "PASS"
    assert safe["requests_count"] == 2
    assert safe["qualified_notice_count"] == 1
    assert safe["plugin_diagnostic"] == {"source": "milan", "http_status": 200}
    assert set(safe).isdisjoint({
        "https_requests", "qualified_notices", "ingest_errors", "reason",
        "error_type", "public_query",
    })
    published = json.dumps(safe, ensure_ascii=False)
    for secret in ("PRIVATE", "SECRET", "/home/", "?token=", "Bearer"):
        assert secret not in published


def test_published_evidence_rejects_untrusted_types_and_unknown_source():
    script = script_module()
    assert script.public_report({
        "source": "../bad", "result": "PASS", "reason": "PRIVATE"
    }) == {"result": "BLOCKED", "error_code": "invalid_evidence"}
    safe = script.public_report({
        "source": "ecole-loisirs", "result": {"text": "PRIVATE"},
        "requests_count": True, "accepted": -1, "qualified_notice_count": 10**15,
        "timeout_seconds": float("nan"), "source_commit": "PRIVATE_TOKEN",
        "core_version": "PRIVATE_TOKEN", "error_code": "PRIVATE_TOKEN",
        "plugin_diagnostic": {"source": "ecole-loisirs", "http_status": 200,
                              "error_type": "PRIVATE_TOKEN"},
    })
    assert safe == {
        "result": "BLOCKED", "source": "ecole-loisirs",
        "allowed_host": "www.ecoledesloisirs.fr",
        "plugin_diagnostic": {"source": "ecole-loisirs", "http_status": 200},
    }


def test_main_exception_never_serializes_private_message(monkeypatch, capsys):
    script = script_module()

    def poisoned_qualify(_args, _report):
        raise RuntimeError(
            "Cookie=PRIVATE_TOKEN /home/alice/books?secret=SECRET_TOKEN"
        )

    monkeypatch.setattr(script, "qualify", poisoned_qualify)
    assert script.main(["--source", "milan", "--confirm-live"]) == 1
    published = capsys.readouterr().out
    safe = json.loads(published)
    assert safe["result"] == "BLOCKED"
    assert safe["error_code"] == "execution_failed"
    for secret in ("PRIVATE", "/home/alice", "Cookie=", "SECRET_TOKEN"):
        assert secret not in published


def test_pass_requires_nonzero_request_and_qualified_notice_counts():
    script = script_module()
    for report in (
        {"source": "milan", "result": "PASS", "requests_count": 0,
         "qualified_notice_count": 2},
        {"source": "milan", "result": "PASS", "requests_count": 1,
         "qualified_notice_count": 0},
        {"source": "milan", "result": "PASS", "qualified_notice_count": 2},
    ):
        safe = script.public_report(report)
        assert safe["result"] == "BLOCKED"
        assert safe["error_code"] == "invalid_evidence"
