import httpx
import pytest

from cifereca_bnf_pret_health_replay import parse_projection


def test_replay_adapter_only_reads_exact_activity():
    def read(method, url):
        assert (method, url) == ("GET", "https://pret.bnf.fr/my_profile/activity")
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b'<section class="active-loans"></section>')
    assert parse_projection("loans", read) == ()
    assert parse_projection("search", lambda *_: pytest.fail("unexpected read")) is None


def test_replay_adapter_positive():
    def read(*_):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              content=b'<section class="active-loans"><div class="js-loan"><a href="/loans/loan1">Pret</a><a href="/resources/book1">Livre</a></div></section>')
    assert parse_projection("loans", read) == (("loan1", "book1", "Livre", ""),)
