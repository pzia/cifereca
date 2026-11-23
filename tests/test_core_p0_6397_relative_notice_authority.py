"""#6397: relative catalogue notices may not switch origin."""
from cifereca.catalogue_link import catalogue_notice_url

BASE = "https://catalogue.example.org/catalogue/"

def test_scheme_relative_external_authority_rejected():
    assert catalogue_notice_url({"friendly_url": "//other.example.org/books/1"}, base=BASE) is None

def test_scheme_relative_same_authority_accepted():
    assert catalogue_notice_url({"friendly_url": "//catalogue.example.org/books/1"}, base=BASE) == "https://catalogue.example.org/books/1"

def test_local_relative_notice_still_accepted():
    assert catalogue_notice_url({"friendly_url": "../books/1"}, base=BASE) == "https://catalogue.example.org/books/1"

def test_absolute_attested_external_link_keeps_existing_policy():
    assert catalogue_notice_url({"friendly_url": "https://other.example.org/books/1"}, base=BASE) == "https://other.example.org/books/1"
