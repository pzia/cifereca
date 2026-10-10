import importlib.util
import pathlib
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx

HERE = pathlib.Path(__file__).resolve().parent
MODULE = HERE.parent / "gallimard_youth.py"
spec = importlib.util.spec_from_file_location("gallimard_youth", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = (HERE / "fixtures" / "catalogue_loup_age11_numerique.html").read_text(encoding="utf-8")

    def test_all_eighteen_published_editions(self):
        stats = {}
        rows = mod.parse_listing(self.sample, diagnostic=stats)
        self.assertEqual(len(rows), 18)
        self.assertEqual(stats["candidate_count"], 18)
        self.assertEqual(stats["declared_count"], 18)
        self.assertEqual(len({r["isbn13"] for r in rows}), 18)
        self.assertEqual(stats["age_qualified_count"], 18)
        self.assertTrue(all(not r.get("cover_url") for r in rows))  # locally saved images
        self.assertEqual(rows[0]["title"], "Dans la gueule du loup")
        self.assertEqual(rows[0]["isbn13"], "9782075202930")
        self.assertEqual(rows[0]["age_ranges"][0]["age_min"], 10)
        self.assertEqual(rows[0]["age_ranges"][0]["age_max"], 12)
        self.assertEqual(rows[0]["authors"], ("Michael Morpurgo",))
        self.assertEqual(rows[0]["metadata"]["illustrators"], ("Barroux",))
        self.assertEqual(rows[1]["metadata"]["subtitle"], "Bastian, loup solitaire et autres contes")
        self.assertEqual(rows[1]["age_ranges"][0]["age_max"], None)
        self.assertEqual(rows[0]["metadata"]["publisher_price_label"], "7.9 €")

    def test_real_age_label_and_edition_identity(self):
        rows = mod.parse_listing(self.sample)
        wolf_eye = [r for r in rows if r["title"] == "L'œil du loup"]
        self.assertEqual(len(wolf_eye), 2)
        self.assertNotEqual(wolf_eye[0]["record_id"], wolf_eye[1]["record_id"])
        self.assertTrue(all(r["age_ranges"][0]["age_kind"] == "publisher" for r in rows))

    def test_unsafe_urls_and_bad_isbn_rejected(self):
        s = self.sample.replace("9782075202930", "3127020509874")
        rows = mod.parse_listing(s)
        self.assertEqual(len(rows), 17)
        s = self.sample.replace("https://www.gallimard-jeunesse.fr/9782075202930/dans-la-gueule-du-loup.html",
                                "https://attacker.example/9782075202930/dans-la-gueule-du-loup.html")
        self.assertEqual(len(mod.parse_listing(s)), 17)
        self.assertEqual(mod.allowed_url("//attacker.example/redirect"), "")
        self.assertEqual(mod.cover_url("catalogue_files/J04968_002_6QEl.jpg"), "")
        self.assertEqual(mod.cover_url("https://evil.example/cover.jpg"), "")
        self.assertEqual(mod.cover_url("https://www.gallimard-jeunesse.fr/cover.jpg"),
                         "https://www.gallimard-jeunesse.fr/cover.jpg")

    def test_age_validation(self):
        self.assertEqual(mod.parse_age("de 9 à 12 ans"), (9, 12, "de 9 à 12 ans"))
        self.assertEqual(mod.parse_age("à partir de 11 ans"), (11, None, "à partir de 11 ans"))
        self.assertIsNone(mod.parse_age("à partir de 18 ans"))
        self.assertIsNone(mod.parse_age("de 13 à 18 ans"))
        self.assertIsNone(mod.parse_age("indéterminé"))

    def test_reject_unknown_layout_but_accept_true_zero(self):
        with self.assertRaises(mod.InvalidCatalogueStructureError):
            mod.parse_listing("<html><h1>Request Rejected</h1></html>")
        self.assertEqual(mod.parse_listing('<main><span class="SearchResults-count">0</span></main>'), ())

    def test_pagination_only_on_attested_next_link(self):
        self.assertEqual(mod.next_cursor(self.sample, current_page=1, query="loup", age=11), "")
        link = '<a rel="next" href="/catalogue.html?text=loup&amp;age%5B0%5D=11&amp;page=2">Suivant</a>'
        self.assertEqual(mod.next_cursor(link, current_page=1, query="loup", age=11), "2")
        self.assertEqual(mod.next_cursor(link, current_page=1, query="chat", age=11), "")
        self.assertEqual(mod.next_cursor(link.replace('page=2','page=50'), current_page=1, query="loup", age=11), "")


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = (HERE / "fixtures" / "catalogue_loup_age11_numerique.html").read_text(encoding="utf-8")

    def test_single_bounded_request_and_contract(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, text=self.sample, headers={"content-type": "text/html; charset=UTF-8"})
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            source = mod.GallimardYouthSource(client=client)
            self.assertEqual(source.source_info()["source_id"], "gallimard-jeunesse")
            result = source.browse(text_filter="loup", age=11, limit=7)
            self.assertEqual(len(result["items"]), 7)
            self.assertEqual(result["next_cursor"], "")
            self.assertEqual(result["collection_request_count"], 1)
            self.assertEqual(len(calls), 1)
            qs = parse_qs(urlsplit(calls[0]).query)
            self.assertEqual(qs["text"], ["loup"])
            self.assertEqual(qs["age[0]"], ["11"])
            self.assertEqual(source.diagnostic_snapshot()["state"], "items")

    def test_rejected_response_does_not_mean_empty_catalogue(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403, text="blocked"))) as client:
            source = mod.GallimardYouthSource(client=client)
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                source.search("loup")
            self.assertEqual(source.diagnostic_snapshot()["state"], "error")
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text="Request Rejected", headers={"content-type":"text/html"}))) as client:
            source = mod.GallimardYouthSource(client=client)
            with self.assertRaises(mod.InvalidCatalogueStructureError):
                source.search("loup")
            self.assertEqual(source.diagnostic_snapshot()["state"], "invalid")

    def test_redirect_refused_and_no_network_for_bad_age(self):
        calls = []
        def handler(request):
            calls.append(1)
            return httpx.Response(302, headers={"location": "https://evil.example"})
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            source = mod.GallimardYouthSource(client=client)
            with self.assertRaises(ValueError):
                source.browse(age=18)
            self.assertEqual(calls, [])
            with self.assertRaisesRegex(RuntimeError, "302"):
                source.search("test")
            self.assertEqual(len(calls), 1)

    def test_cancel_before_request(self):
        with httpx.Client(transport=httpx.MockTransport(lambda _: self.fail("should not query"))) as client:
            source = mod.GallimardYouthSource(client=client)
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                source.browse(cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
