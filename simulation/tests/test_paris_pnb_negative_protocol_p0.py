"""P0 #771: deterministic negative PNB/Paris loan, renewal and ACSM HTTP.

Only the existing fake Paris service on an ephemeral loopback port; no real
provider, identity, borrowed book, credentials, DRM or network call.
"""
from __future__ import annotations

import http.client
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from simulation.tests.test_fake_services_contract import PARIS, RunningServer


class ParisPNBNegativeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        cache = PARIS.DatasetCache(
            Path(self.directory.name) / "fake-paris", 24, 0, 0, 0,
            0.70, 20261015, True,
        )
        self.cache = cache
        digital = cache.connect().execute(
            "SELECT record_id FROM books WHERE medium='digital' AND borrowable=1 "
            "ORDER BY book_id LIMIT 1"
        ).fetchone()
        physical = cache.connect().execute(
            "SELECT record_id FROM books WHERE medium='physical' ORDER BY book_id LIMIT 1"
        ).fetchone()
        assert digital is not None and physical is not None
        self.digital = str(digital[0])
        self.physical = str(physical[0])
        self.context = RunningServer(
            PARIS.Handler, PARIS.ParisFakeApp(cache, "fake-pnb-account", "fake-password"),
        )
        self.http = self.context.__enter__()

    def tearDown(self):
        try:
            self.context.__exit__(None, None, None)
        finally:
            self.directory.cleanup()

    def count_loans(self):
        return self.cache.connect().execute(
            "SELECT COUNT(*) FROM loans WHERE loan_id LIKE 'loan-live-%'"
        ).fetchone()[0]

    def redirect(self, record_id):
        # Disable automatic urllib redirect-following: the fake ACSM hop is
        # asserted separately. A URI here does not imply DRM fulfillment.
        inner = "/fake/loan-book.ashx?ean=" + quote(record_id, safe="")
        target = "/numerique/ASSARedirect.ashx?url=" + quote(inner, safe="")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.http.server.server_port, timeout=3,
        )
        try:
            connection.request("GET", target)
            response = connection.getresponse()
            status, location = response.status, response.getheader("Location")
            response.read()
            return status, location
        finally:
            connection.close()

    def post_json(self, route, body):
        request = Request(
            self.http.base + PARIS.ACCOUNT_SERVICE + route,
            data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_pnb_borrow_redirect_is_bound_to_one_eligible_digital_book(self):
        self.assertEqual(self.count_loans(), 0)
        status, destination = self.redirect(self.digital)
        self.assertEqual(status, 302)
        self.assertEqual(destination, "/fake-delivery/" + quote(self.digital) + ".acsm")
        self.assertEqual(self.count_loans(), 1)
        # The same anonymous synthetic account and record cannot create
        # another active loan or bypass the limited license count.
        again, again_destination = self.redirect(self.digital)
        self.assertEqual((again, again_destination), (status, destination))
        self.assertEqual(self.count_loans(), 1)

    def test_unknown_physical_and_exhausted_pnb_records_fail_without_new_loan(self):
        for record in ("missing-fake-record", self.physical):
            with self.subTest(record=record):
                status, destination = self.redirect(record)
                self.assertEqual(status, 404)
                self.assertIsNone(destination)
                self.assertEqual(self.count_loans(), 0)

        self.cache.connect().execute(
            "UPDATE books SET borrowable=0, licenses=0 WHERE record_id=?",
            (self.digital,),
        )
        self.cache.connect().commit()
        status, destination = self.redirect(self.digital)
        self.assertEqual(status, 409)
        self.assertIsNone(destination)
        self.assertEqual(self.count_loans(), 0)

    def test_renew_and_return_reject_unknown_replayed_or_already_returned_loans(self):
        uid = ""  # only fake primary household account
        renew = "/RenewLoans"
        returned = "/ReturnDigitalLoan"
        missing = "fake-unknown-loan"
        for route, payload in (
            (renew, {"userUniqueIdentifier": uid, "loans": [{"Id": missing}]}),
            (returned, {"userUniqueIdentifier": uid, "id": missing}),
        ):
            with self.subTest(route=route):
                status, reply = self.post_json(route, payload)
                self.assertEqual(status, 409)
                self.assertFalse(reply["success"])
        self.assertEqual(self.count_loans(), 0)

        status, _ = self.redirect(self.digital)
        self.assertEqual(status, 302)
        row = self.cache.connect().execute(
            "SELECT loan_id FROM loans WHERE record_id=? AND returned_at IS NULL",
            (self.digital,),
        ).fetchone()
        self.assertIsNotNone(row)
        loan = str(row[0])
        payload = {"userUniqueIdentifier": uid, "loans": [{"Id": loan}]}
        ok, reply = self.post_json(renew, payload)
        self.assertEqual(ok, 200)
        self.assertTrue(reply["success"])
        replay, reply = self.post_json(renew, payload)
        self.assertEqual(replay, 409)
        self.assertFalse(reply["success"])

        ok, reply = self.post_json(
            returned, {"userUniqueIdentifier": uid, "id": loan},
        )
        self.assertEqual(ok, 200)
        self.assertTrue(reply["success"])
        replay, reply = self.post_json(
            returned, {"userUniqueIdentifier": uid, "id": loan},
        )
        self.assertEqual(replay, 409)
        self.assertFalse(reply["success"])
        self.assertEqual(self.count_loans(), 1)

    def test_opt_in_pnb_http_failures_are_stateless_and_recover(self):
        # Existing PNB endpoints only: fault is in the query, never in
        # credentials, provider HTML or any persistent loan state.
        endpoints = (
            ("/ListLoans", "GET", None),
            ("/DownloadDigitalDocument", "POST", {"ean": self.digital}),
            ("/RenewLoans", "POST", {"userUniqueIdentifier": "", "loans": []}),
            ("/ReturnDigitalLoan", "POST", {"userUniqueIdentifier": "", "id": "missing"}),
        )
        for route, method, payload in endpoints:
            for fault, expected in (("expired", 401), ("429", 429), ("503", 503)):
                with self.subTest(route=route, fault=fault):
                    path = PARIS.ACCOUNT_SERVICE + route + "?ux_fault=" + fault
                    if method == "POST":
                        status, reply = self.post_json(route + "?ux_fault=" + fault, payload)
                        self.assertFalse(reply["success"])
                    else:
                        with self.assertRaises(HTTPError) as caught:
                            self.http.get(path)
                        status = caught.exception.code
                    self.assertEqual(status, expected)
                    self.assertEqual(self.count_loans(), 0)
        with self.assertRaises(HTTPError) as caught:
            self.http.get(PARIS.ACCOUNT_SERVICE + "/ListLoans?ux_fault=429")
        self.assertEqual(caught.exception.headers.get("Retry-After"), "2")
        self.assertEqual(self.http.get("/health?ux_fault=503")[0], 200)
        status, reply = self.post_json("/DownloadDigitalDocument?ux_fault=unknown", {"ean": self.digital})
        self.assertEqual(status, 200)
        self.assertTrue(reply["success"])
        self.assertEqual(self.count_loans(), 0)
        self.assertFalse(self.cache.acsm_path(self.digital).exists())

        # The borrow redirect is mutable: all injected failures must happen
        # before a loan is created. Its healthy fallback remains unchanged.
        from urllib.parse import quote as urlquote
        redirect = ("/numerique/ASSARedirect.ashx?url=" + urlquote(
            "/fake/loan-book.ashx?ean=" + urlquote(self.digital, safe=""), safe="",
        ))
        for fault, expected in (("expired", 401), ("429", 429), ("503", 503)):
            with self.subTest(borrow=fault), self.assertRaises(HTTPError) as caught:
                self.http.get(redirect + "&ux_fault=" + fault)
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(self.count_loans(), 0)
        self.assertFalse(self.cache.acsm_path(self.digital).exists())
        self.assertEqual(self.redirect(self.digital)[0], 302)
        self.assertEqual(self.count_loans(), 1)

    def test_pnb_delivery_requires_existing_digital_record_no_real_fulfillment(self):
        endpoint = "/DownloadDigitalDocument"
        for record in ("missing-fake-record", self.physical):
            status, reply = self.post_json(endpoint, {"ean": record})
            self.assertEqual(status, 404)
            self.assertFalse(reply["success"])
            with self.assertRaises(HTTPError) as denied:
                self.http.get("/fake-delivery/" + quote(record) + ".acsm")
            self.assertEqual(denied.exception.code, 404)
        ok, reply = self.post_json(endpoint, {"ean": self.digital})
        self.assertEqual(ok, 200)
        self.assertTrue(reply["success"])
        path = reply["d"]
        self.assertEqual(path, "/fake-delivery/" + quote(self.digital) + ".acsm")
        status, headers, xml = self.http.get(path)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/vnd.adobe.adept+xml")
        self.assertIn(b"<fulfillmentToken", xml)
        self.assertIn(b"Aucun DRM", xml)
        self.assertEqual(self.count_loans(), 0)


if __name__ == "__main__":
    unittest.main()
