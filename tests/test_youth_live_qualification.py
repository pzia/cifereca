"""No-network regressions for the explicitly opt-in youth HTTPS check."""
from __future__ import annotations

import importlib.util
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
    assert script.main(["--source", "booktrust"]) == 2
    assert '"result": "NOT_RUN"' in capsys.readouterr().out


def test_input_limits_and_hard_request_budget(capsys):
    script = script_module()
    for argv in (
        ["--source", "milan", "--age", "18", "--confirm-live"],
        ["--source", "milan", "--age", "-1", "--confirm-live"],
        ["--source", "milan", "--limit", "4", "--confirm-live"],
        ["--source", "booktrust", "--timeout", "30", "--confirm-live"],
    ):
        assert script.main(argv) == 2
    assert "INVALID_INPUT" in capsys.readouterr().out
    assert script.plan(script.parser().parse_args(["--source", "milan"]))["request_budget"] == 9
    assert script.plan(script.parser().parse_args(["--source", "booktrust"]))["request_budget"] == 1


def test_evidence_requires_real_source_label_and_origin():
    script = script_module()
    age = NS(source_id="milan", age_kind="publisher", source_label="À partir de 8 ans",
             age_min=8, age_max=None)
    book = dict(title="Lumilane", identifiers={"isbn": "9782408060381"},
                age_ranges=(age,), notice_url="https://www.editionsmilan.com/livres/97384-lumilane/",
                provenance=("milan",))
    assert len(script.evidence_for((NS(**book),), "milan")) == 1
    assert not script.evidence_for((NS(**(book | {"provenance": ()})),), "milan")
    assert not script.evidence_for((NS(**(book | {"provenance": ("booktrust",)})),), "milan")
    assert not script.evidence_for((NS(**(book | {"notice_url": "https://evil.example/"})),), "milan")
    assert not script.evidence_for((NS(**(book | {"identifiers": {}})),), "milan")
    wrong = NS(**(vars(age) | {"source_label": ""}))
    assert not script.evidence_for((NS(**(book | {"age_ranges": (wrong,)})),), "milan")
    interest = NS(source_id="booktrust", age_kind="interest", source_label="Interest age 7-9",
                  age_min=7, age_max=9)
    bt = NS(title="BookTrust title", identifiers={}, age_ranges=(interest,),
            notice_url="https://www.booktrust.org.uk/book-recommendations/bookfinder/example/",
            provenance=("booktrust",))
    assert len(script.evidence_for((bt,), "booktrust")) == 1


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
    assert script.plugin_diagnostic_for(source, "booktrust") == {}
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
    assert report["https_requests"] == ["https://www.editionsmilan.com/livres/"]
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
    assert 'report["public_query"] = "loup"' in source
    assert '"max_requests": 1' in source
