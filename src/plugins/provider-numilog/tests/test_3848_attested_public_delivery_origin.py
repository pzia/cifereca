"""#3848: source-attested deliveries are not domain-whitelisted, but remain SSRF-safe.

No network access or real signed URL: DNS is a fully synthetic fixture.
"""
import socket
from unittest.mock import patch

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog import _checked_attested_network_url

TRUSTED = {("https", "www.numilog.com", 443)}
DELIVERY = "https://new-public-distributor.example.test/delivery?token=FICTIONAL%2FKEEP"


def _dns(_host, _port, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.99", 443))]


def test_attested_external_origin_is_not_blocked_by_static_list():
    # A globally routable DNS answer is required, even for an unknown domain.
    with patch("cifereca_numilog.socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
    ]) as lookup:
        assert _checked_attested_network_url(DELIVERY, TRUSTED) == DELIVERY
        lookup.assert_called_once()


@pytest.mark.parametrize("resolved", [
    "127.0.0.1", "10.10.1.7", "169.254.1.1", "192.168.1.2",
    "::1", "fc00::1", "2001:db8::1",
])
def test_attested_delivery_rejects_any_nonpublic_dns_address(resolved):
    with patch("cifereca_numilog.socket.getaddrinfo", return_value=[
        (socket.AF_INET6 if ":" in resolved else socket.AF_INET, socket.SOCK_STREAM,
         6, "", (resolved, 443)),
    ]):
        with pytest.raises(ProviderError, match="non publique"):
            _checked_attested_network_url(DELIVERY, TRUSTED)


def test_attested_delivery_rejects_mixed_public_and_private_dns():
    with patch("cifereca_numilog.socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]):
        with pytest.raises(ProviderError, match="non publique"):
            _checked_attested_network_url(DELIVERY, TRUSTED)


@pytest.mark.parametrize("unsafe", [
    "http://new-public-distributor.example.test/offer",
    "https://user:password@new-public-distributor.example.test/offer",
    "https://127.0.0.1/offer",
    "https://new-public-distributor.example.test/offer#fragment",
    "https://new-public-distributor.example.test/del\\nivery",
])
def test_attested_delivery_rejects_unsafe_url_without_dns(unsafe):
    with patch("cifereca_numilog.socket.getaddrinfo") as lookup:
        with pytest.raises(ProviderError, match="refusée"):
            _checked_attested_network_url(unsafe, TRUSTED)
        lookup.assert_not_called()
