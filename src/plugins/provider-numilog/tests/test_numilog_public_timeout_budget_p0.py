"""P0 #1886: hostile timeout inputs never open an anonymous HTTP client."""
import math

import pytest

import cifereca_numilog_public_client as client
from cifereca.plugin_api import ProviderCapabilityError


@pytest.mark.parametrize("bad", [
    None, True, False, "15", [], {}, 0, -1, -0.01, 30.00001,
    float("nan"), float("inf"), -float("inf"), 10**1000,
])
@pytest.mark.parametrize("endpoint", ["search", "detail"])
def test_invalid_timeout_fails_before_http(monkeypatch, bad, endpoint):
    calls = []
    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ProviderCapabilityError, match="invalid_public_timeout"):
        if endpoint == "search":
            client.fetch_public_search_url(
                client.public_search_url("jefferson"), timeout=bad,
            )
        else:
            client.fetch_public_detail(
                "https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook",
                timeout=bad,
            )
    assert not calls


@pytest.mark.parametrize("valid", [0.01, 1, 15, 30, 30.0])
def test_timeout_budget_accepts_finite_bounded_values(valid):
    assert client._checked_timeout(valid) == float(valid)


def test_timeout_budget_rejects_huge_positive_integer():
    with pytest.raises(ProviderCapabilityError, match="invalid_public_timeout"):
        client._checked_timeout(10**10000)
