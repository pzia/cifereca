"""Scénarios UX reproductibles contre les vrais plugins Jeunesse, sans réseau."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

SIM = Path(__file__).resolve().parents[1]
REPO = SIM.parent
for path in (SIM, REPO / "src", REPO / "src/plugins/youth-catalog",
             REPO / "src/plugins/youth-source-discovery",
             REPO / "src/plugins/youth-detail-enrichment"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import plugin_scenarios
from youth_discovery import PLUGIN as discovery_plugin
from youth_catalog import normalize_record
from youth_detail_enrichment import YouthDetailEnrichment
from cifereca.plugin_api import BookRef


class IsolatedCatalog:
    def __init__(self, records, scope):
        self.records = records
        self.scope = scope
        self.calls = []
        self.search_calls = []

    def find_cached(self, identity_id, **filters):
        self.calls.append((identity_id, dict(filters)))
        allowed = self.scope.get(identity_id, [])
        candidates = [self.records[n] for n in allowed]
        isbn = filters.get("isbn")
        provider = filters.get("provider")
        item_id = filters.get("source_item_id")
        notice = filters.get("notice_url")
        return tuple(row for row in candidates if (
            (isbn and row.identifiers.get("isbn") == isbn)
            or (provider and item_id and row.identifiers.get("provider") == provider
                and row.identifiers.get("source_item_id") == item_id)
            or (notice and row.notice_url == notice)
        ))[:filters.get("limit", 20)]

    def search_cached(self, identity_id, query):
        self.search_calls.append((identity_id, query))
        raise AssertionError("Le plugin moderne ne doit jamais matcher sur le titre")


class LocalContext:
    def __init__(self, catalog):
        self.catalog = catalog
        self.service_calls = []

    def service(self, service_id):
        self.service_calls.append(service_id)
        return self.catalog if service_id == "catalog.youth" else None


class YouthWorkflowContractTests(unittest.TestCase):
    def read_cases(self, directory):
        plugin_scenarios.create(directory)
        discovery = json.loads((directory / "youth-discovery.json").read_text(encoding="utf-8"))
        enrichment = json.loads((directory / "youth-enrichment.json").read_text(encoding="utf-8"))
        return discovery, enrichment

    def test_real_source_discovery_deduplicates_rejects_private_and_requires_review(self):
        with tempfile.TemporaryDirectory() as td:
            cases, _ = self.read_cases(Path(td))
            called = []
            inspected = []
            def search(query, limit=20):
                called.append((query, limit))
                return list(cases["search_results"])

            def fetch(url):
                inspected.append(url)
                if url in cases["fail_fetch_urls"]:
                    raise RuntimeError("fictional source unavailable")
                return cases["fetch_pages"][url]

            report = discovery_plugin.discover(cases["query"], search=search, fetch=fetch,
                                               inspect_limit=10)
            self.assertEqual(len(called), 3)
            self.assertEqual(len(inspected), 4)
            self.assertEqual(report.inspected_pages, 3)
            self.assertEqual(len(report.fetch_errors), 1)
            self.assertEqual([r.domain for r in report.candidates], cases["expected_domains"])
            self.assertEqual(len(report.candidates[0].sample_urls), 2)
            self.assertTrue(all(r.status == "strong" for r in report.candidates))
            self.assertEqual([e.age_min_months for e in report.candidates[1].evidence], [18])
            self.assertTrue(all("127.0.0.1" not in r.domain and
                                "credential.example" not in r.domain for r in report.candidates))
            dossier = discovery_plugin.export_dossier(report)
            self.assertTrue(all(row["manual_review_required"] for row in dossier["candidates"]))
            self.assertTrue(all(len(row["fingerprint"]) == 24 for row in dossier["candidates"]))
            self.assertEqual(dossier, discovery_plugin.export_dossier(
                discovery_plugin.discover(cases["query"], search=search, fetch=fetch,
                                          inspect_limit=10)))
            # Inspection désactivée : seule la preuve explicite de recherche demeure.
            minimal = discovery_plugin.discover(cases["query"], search=search,
                                                fetch=fetch, inspect_limit=0)
            self.assertEqual([row.domain for row in minimal.candidates], ["lecture.example"])
            self.assertEqual(minimal.inspected_pages, 0)
            self.assertTrue(cases["never_register_automatically"])

    def test_true_cached_detail_enrichment_respects_isbn_conflicts_and_identities(self):
        with tempfile.TemporaryDirectory() as td:
            _, cases = self.read_cases(Path(td))
            records = plugin_scenarios.youth_records()["records"]
            normalized = {
                int(row["source_item_id"]): normalize_record(row, source_id="ux-youth")
                for row in records
            }
            catalog = IsolatedCatalog(normalized, cases["cache_scope"])
            context = LocalContext(catalog)
            plugin = YouthDetailEnrichment().activate(context)
            results = {}
            for name, row in cases["cases"].items():
                book = BookRef(row["book_id"], row["title"], identifiers=row["identifiers"])
                result = plugin.cached_detail(book, identity_id=row["identity_id"])
                results[name] = result
                if row["expected_record"] is None:
                    self.assertIsNone(result, name)
                else:
                    self.assertIsNotNone(result, name)
                    self.assertEqual(result.book_id, row["book_id"])
                    self.assertEqual(result.identity_id, row["identity_id"])
                    self.assertEqual(result.source.metadata["match"], row["expected_match"])
                    self.assertEqual(result.sections[0].title, "Jeunesse")
                    self.assertTrue(result.freshness_token)
            self.assertEqual(catalog.search_calls, [])
            self.assertEqual(len(catalog.calls), 4)  # exact, conflict, ambiguous, wrong identity
            self.assertEqual(results["isbn_exact"].sections[0].fields[0].label,
                             "Âge recommandé")
            self.assertTrue(results["isbn_exact"].actions)
            self.assertEqual(results["isbn_exact"].actions[0].uri,
                             "https://ux-fixtures.invalid/books/2")
            self.assertNotEqual(results["isbn_exact"].freshness_token,
                                results["edition_conflict"].freshness_token)
            self.assertEqual(set(cases["identities"]), set(cases["cache_scope"]))
            self.assertTrue(cases["never_fetch_network"])

    def test_faulty_cache_does_not_trigger_network_or_title_fallback(self):
        class BrokenCatalog:
            def find_cached(self, identity_id, **filters):
                raise RuntimeError("offline cache simulation")
            def search_cached(self, identity_id, query):
                raise AssertionError("unwanted fallback")
        plugin = YouthDetailEnrichment().activate(LocalContext(BrokenCatalog()))
        self.assertIsNone(plugin.cached_detail(
            BookRef("book-1", "A local title", identifiers={"isbn": plugin_scenarios.ISBN_B}),
            identity_id="ux-camille",
        ))

    def test_scenarios_reproducible_across_distinct_directories(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = plugin_scenarios.create(Path(a))
            second = plugin_scenarios.create(Path(b))
            self.assertEqual(first, second)
            self.assertEqual(len(first["files"]), 9)
            self.assertTrue(all(first["files"].get(name) for name in (
                "youth-discovery.json", "youth-enrichment.json")))


if __name__ == "__main__":
    unittest.main()
