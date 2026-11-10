"""P1 #4979: untrusted cover URLs may not address local/private hosts."""
import pytest

from cifereca.core.covers import CoverCandidate, candidate, resolve_cover


@pytest.mark.parametrize("url", [
    "http://localhost/a.jpg",
    "http://LOCALHOST./a.jpg",
    "https://reader.local/a.jpg",
    "https://sub.localhost/a.jpg",
    "http://127.0.0.1/a.jpg",
    "https://10.23.4.5/a.jpg",
    "https://192.168.1.25/a.jpg",
    "http://169.254.12.3/a.jpg",
    "http://[::1]/a.jpg",
    "http://[fc00::2]/a.jpg",
    "http://[fe80::1]/a.jpg",
    "https://0.0.0.0/a.jpg",
])
def test_private_cover_target_rejected_by_constructor_and_resolver(url):
    assert candidate(url, tier="catalog", source="remote") is None
    resolved = resolve_cover([CoverCandidate(url, "catalog", "remote")])
    assert resolved.status == "missing"
    assert not resolved.url


def test_public_urls_and_local_embedded_proofs_keep_priority():
    origin = candidate(
        "https://covers.example.org/book.jpg", tier="origin", source="source"
    )
    catalogue = candidate(
        "https://8.8.8.8/book.jpg", tier="catalog", source="public"
    )
    embedded = candidate(
        "file:///home/reader/.cache/covers/a.jpg",
        tier="embedded", source="embedded",
    )
    assert origin and catalogue and embedded
    assert resolve_cover((catalogue, origin)).url == origin.url
    assert resolve_cover((catalogue, origin, embedded)).url == embedded.url


def test_private_cover_does_not_block_other_attested_candidate():
    bad = CoverCandidate("http://127.0.0.1/admin", "embedded", "forged")
    good = candidate(
        "https://covers.example.org/real.jpg", tier="catalog", source="public"
    )
    assert good
    assert resolve_cover((bad, good)).url == good.url
