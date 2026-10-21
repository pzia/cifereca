"""P0 #2530: fictional XML stays useful without publishing private prose."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import _body, prepare_reviewed_zip

ORIGIN = "https://books.example.org"


def test_saved_xml_preserves_case_ids_and_cross_protocol_relations(tmp_path):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [
        PrivateExchange(
            1, "POST", url=ORIGIN + "/books?AccountId=account-FICT&BookId=214",
            request_body='<Request><AccountId>account-FICT</AccountId><BookId>214</BookId></Request>',
            request_headers={"content-type": "application/xml"},
            response_body=(
                '<Response><Book BookId="214"><AccountId>account-FICT</AccountId>'
                '<BookId>214</BookId><Format>epub</Format><Title>PRIVATE_TITLE</Title>'
                '<password><BookId>214</BookId></password>'
                '<access_token>FAKE_SECRET_VALUE</access_token>'
                '<Download href="https://books.example.org/file?signature=FAKE_SIGNATURE"/>'
                '</Book></Response>'
            ), response_headers={"content-type": "application/xml"}, status=200,
        ),
        PrivateExchange(
            2, "GET", url=ORIGIN + "/books?AccountId=account-FICT&BookId=214",
            response_body='{"AccountId":"account-FICT","BookId":214}',
            response_headers={"content-type": "application/json"}, status=200,
        ),
    ]
    review = IdentifierReview(trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "keep" if candidate.value == "214" else "anonymize")
    plan = prepare_reviewed_zip(review)
    assert not list(tmp_path.iterdir())
    plan.preview()
    with ZipFile(tmp_path / plan.save(tmp_path, user_confirmed=True)) as archive:
        first = json.loads(archive.read("exchanges/0001.json"))
        second = json.loads(archive.read("exchanges/0002.json"))
        assert first["bodies"]["request"]["state"] == "captured_xml_structure"
        request = ET.fromstring(archive.read(first["bodies"]["request"]["file"]))
        response = ET.fromstring(archive.read(first["bodies"]["response"]["file"]))
        body = json.loads(archive.read(second["bodies"]["response"]["file"]))
        book = response.find("Book")
        account = body["AccountId"]
        assert account.startswith("PSEUDO_")
        assert request.findtext("AccountId") == book.findtext("AccountId") == account
        assert request.findtext("BookId") == book.findtext("BookId") == book.get("BookId") == "214"
        assert body["BookId"] == 214
        assert parse_qs(urlsplit(first["url"]).query) == {"AccountId": [account], "BookId": ["214"]}
        assert book.findtext("Format") == "epub"
        assert book.findtext("Title") == "[TEXT]"
        assert book.findtext("password/BookId") == "[SECRET]"
        assert book.findtext("access_token") == "[SECRET]"
        assert book.find("Download").get("href") == "[TEXT]"
        payload = b"\n".join(archive.read(name) for name in archive.namelist())
        for denied in (b"account-FICT", b"PRIVATE_TITLE", b"FAKE_SECRET_VALUE", b"FAKE_SIGNATURE"):
            assert denied not in payload
    review.destroy()
    trace.destroy()


@pytest.mark.parametrize("decision", ("keep", "anonymize"))
def test_xml_entity_encoded_identifier_has_one_decision_and_valid_projection(decision):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [PrivateExchange(
        1, "GET", url=ORIGIN + "/books?BookId=part%26edition",
        response_body='<Books><BookId>part&amp;edition</BookId></Books>',
        response_headers={"content-type": "text/xml"}, status=200,
    )]
    review = IdentifierReview(trace)
    assert [c.value for c in review.candidates()] == ["part&edition"]
    candidate = review.candidates()[0]
    review.decide(candidate.key, decision)
    files = {m["name"]: m["text"] for m in prepare_reviewed_zip(review).preview()["members"]}
    xml = ET.fromstring(files["bodies/0001-response.txt"])
    exchange = json.loads(files["exchanges/0001.json"])
    expected = "part&edition" if decision == "keep" else "PSEUDO_" + candidate.key
    assert xml.findtext("BookId") == parse_qs(urlsplit(exchange["url"]).query)["BookId"][0] == expected
    review.destroy()
    trace.destroy()


def test_xml_public_namespaces_preserved_private_namespaces_opaque_and_distinct():
    raw = (
        '<a:feed xmlns:a="http://www.w3.org/2005/Atom" '
        'xmlns:p="urn:PRIVATE_NAMESPACE" xmlns:q="urn:OTHER_PRIVATE_NAMESPACE" '
        'xmlns:f="urn:cifereca:xml-namespace-0001">'
        '<p:BookId>214</p:BookId><q:BookId>214</q:BookId><p:BookId>214</p:BookId>'
        '<f:BookId>214</f:BookId><!-- PRIVATE_COMMENT -->'
        '<?private PRIVATE_INSTRUCTION?></a:feed>'
    )
    shaped, state = _body(raw, "application/atom+xml", frozenset(("214",)))
    assert state == "captured_xml_structure"
    root = ET.fromstring(shaped)
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"
    tags = [child.tag for child in root]
    assert len(tags) == 4 and tags[0] == tags[2]
    assert len(set(tags)) == 3
    assert all(child.text == "214" for child in root)
    assert "PRIVATE_" not in shaped
    assert "urn:cifereca:xml-namespace-0001" not in shaped


@pytest.mark.parametrize("raw", [
    '<!DOCTYPE r [<!ENTITY x "PRIVATE_ENTITY">]><r>&x;</r>',
    '<!DOCTYPE r SYSTEM "https://private.example.org/external.dtd"><r/>',
    '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///PRIVATE_FILE">]><r>&x;</r>',
    '<r><BookId>214</r>',
    '<r>&undefined;</r>',
    '<p:r/>',
    '<r>' * 18 + '</r>' * 18,
    '<r>' + '<v/>' * 1601 + '</r>',
    '<r ' + ' '.join(f'a{n}="x"' for n in range(33)) + '/>',
    '<r>' + 'x' * 32768 + '</r>',
    '<r>' + '<v>PRIVATE</v>' * 1500 + '</r>',
], ids=["internal-entity", "external-dtd", "external-file", "malformed", "undefined-entity", "unbound-prefix", "depth", "nodes", "attributes", "input-size", "output-size"])
def test_unsafe_malformed_and_over_budget_xml_is_omitted(raw):
    assert _body(raw, "application/xml", frozenset(("214",))) == (None, "body_omitted")


def test_mixed_xml_text_and_tails_do_not_publish_unreviewed_prose():
    shaped, state = _body(
        '<r>PRIVATE_BEFORE<BookId>214</BookId>PRIVATE_AFTER</r>',
        "application/xml", frozenset(("214",)),
    )
    assert state == "captured_xml_structure"
    root = ET.fromstring(shaped)
    assert root.text == "[TEXT]" and root[0].tail == "[TEXT]"
    assert root[0].text == "214" and "PRIVATE" not in shaped
