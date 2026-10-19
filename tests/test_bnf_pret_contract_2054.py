"""Isolated contract fixtures: never contact BnF or exercise a live loan."""
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "src/plugins/provider-bnf-pret/cifereca_bnf_pret_contract.py"
spec = importlib.util.spec_from_file_location("cifereca_bnf_pret_contract", MODULE)
contract = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = contract
spec.loader.exec_module(contract)


def headers(*, current=1, pages=1, total=1, per_page=5):
    return {"X-Current-Page": str(current), "X-Total-Pages": str(pages),
            "X-Total-Items": str(total), "X-Per-Page": str(per_page)}


def resource(i):
    return {"id": f"res-{i}", "title": f"Title {i}", "publisher_name": "P",
            "contributors": [{"first_name": "First", "last_name": "Last", "nature": "author"}],
            "media": [{"id": f"m-{i}", "nature": "epub", "key_type": "isbn13",
                       "key": "9781234567897", "current_holds": 1,
                       "loans": {"available": 0, "total": 2}}],
            "cover": "https://assets.cantook.net/assets/publications/1/cover/medium.jpg",
            "link": "https://pret.bnf.fr/loans/secret?token=do-not-leak"}


def loan(i, member="a"):
    return {"id": f"loan-{i}", "member_id": member, "resource_id": f"res-{i}",
            "expires_at": "2026-11-16T12:00:00Z", "hold_expires_at": None,
            "downloads": ["2026-10-19T12:00:00Z"]}


def test_resource_empty_one_multiple_pages_and_provenance():
    empty = contract.parse_page({"resources": []}, headers(pages=0, total=0), collection="resources")
    assert empty.items == () and empty.next_page is None
    page1 = contract.parse_page({"resources": [resource(1)]}, headers(pages=2, total=2), collection="resources")
    assert page1.next_page == 2 and page1.items[0].owned is False
    assert page1.items[0].media[0].isbn == "9781234567897"
    assert page1.items[0].media[0].available_copies == 0
    assert page1.items[0].author == "First Last"
    page2 = contract.parse_page({"resources": [resource(2)]}, headers(current=2, pages=2, total=2), collection="resources")
    assert page2.next_page is None and page2.items[0].id == "res-2"
    assert "secret" not in repr(page1)


def test_untrusted_cover_and_unknown_availability():
    raw = resource(1)
    raw["cover"] = "https://assets.cantook.net.evil.test/assets/a?token=signed"
    raw["media"][0]["loans"] = None
    row = contract.parse_page({"resources": [raw]}, headers(), collection="resources").items[0]
    assert row.cover_url is None and row.media[0].available_copies is None


@pytest.mark.parametrize("bad_header", [
    {"X-Current-Page": "nan", "X-Total-Pages": "1", "X-Total-Items": "1", "X-Per-Page": "5"},
    headers(current=3, pages=2), headers(total=0, pages=2), headers(total=20, pages=1),
    headers(per_page=0), headers(pages=1001), {},
])
def test_invalid_pagination_fails_closed(bad_header):
    with pytest.raises(contract.BnFPretContractError):
        contract.parse_page({"resources": [resource(1)]}, bad_header, collection="resources")


def test_multi_identity_loans_and_no_download_links():
    a = contract.parse_page({"loans": [loan(1, "a")]}, headers(), collection="loans", expected_member_id="a")
    b = contract.parse_page({"loans": [loan(2, "b")]}, headers(), collection="loans", expected_member_id="b")
    assert a.items[0].id != b.items[0].id
    assert "downloads" not in repr(a)
    with pytest.raises(contract.BnFPretContractError, match="loan_identity_mismatch"):
        contract.parse_page({"loans": [loan(1, "b")]}, headers(), collection="loans", expected_member_id="a")
    with pytest.raises(contract.BnFPretContractError, match="invalid_member_id"):
        contract.parse_page({"loans": [loan(1)]}, headers(), collection="loans")


@pytest.mark.parametrize("status,code", [(401,"auth_required"),(403,"loans_limit_reached"),
                                            (404,"not_found"),(429,"rate_limited"),
                                            (503,"upstream_unavailable")])
def test_typed_auth_quota_and_transport_errors(status, code):
    body = {"errors": ["loans_limit_reached"]} if status == 403 else {"errors": ["signed https://secret.test"]}
    with pytest.raises(contract.BnFPretContractError) as exc:
        contract.parse_page(body, {}, collection="resources", status=status)
    assert exc.value.code == code and "secret" not in str(exc.value)


def test_search_only_documented_filters_no_request_sent():
    assert contract.search_parameters(q="abc", isbn="978") == {"page": "1", "q": "abc", "isbn": "978"}
    with pytest.raises(contract.BnFPretContractError, match="unsupported_search_filter"):
        contract.search_parameters(member_id="a")
    with pytest.raises(contract.BnFPretContractError, match="invalid_page"):
        contract.search_parameters(page=0, q="abc")
    with pytest.raises(contract.BnFPretContractError, match="missing_search_filter"):
        contract.search_parameters()
    assert not contract.READER_CAPABILITIES


@pytest.mark.parametrize("status,created", [(200, False), (201, True)])
def test_loan_creation_status_is_interpretation_only_without_url(status, created):
    parsed = contract.interpret_loan_creation(status, {"url": "https://pret.bnf.fr/loans/sensitive?token=secret",
                                                      "expires_at": "2026-11-16T12:00:00Z"})
    assert parsed.newly_created is created
    assert parsed.expires_at == "2026-11-16T12:00:00Z"
    assert "token" not in repr(parsed) and "secret" not in repr(parsed)


def test_creation_response_rejects_external_origin_and_business_errors():
    with pytest.raises(contract.BnFPretContractError, match="invalid_loan_response"):
        contract.interpret_loan_creation(201, {"url": "https://pret.bnf.fr.evil.test/loans/1"})
    with pytest.raises(contract.BnFPretContractError, match="no_loan_available"):
        contract.interpret_loan_creation(403, {"errors": ["no_loan_available"]})
    assert contract.error_code(403, {"errors": ["loan_hold_expired"]}) == "loan_hold_expired"
    assert contract.error_code(403, {"errors": ["member_downloaded_monthly_limit_reached"]}) == "member_downloaded_monthly_limit_reached"


def test_negative_availability_and_invalid_dates():
    raw = resource(1)
    raw["media"][0]["loans"] = {"available": 3, "total": 2}
    with pytest.raises(contract.BnFPretContractError, match="invalid_availability"):
        contract.parse_page({"resources": [raw]}, headers(), collection="resources")
    raw = loan(1)
    raw["expires_at"] = "not-a-date"
    with pytest.raises(contract.BnFPretContractError, match="invalid_date"):
        contract.parse_page({"loans": [raw]}, headers(), collection="loans", expected_member_id="a")
