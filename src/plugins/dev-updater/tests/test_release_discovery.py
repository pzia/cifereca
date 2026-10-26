"""P2 #794: safe, deterministic GitHub release discovery without HTTP."""
from __future__ import annotations

import json
import httpx

import pytest

from github_releases import (
    DEFAULT_REPOSITORY, MAX_RESPONSE, ReleaseCheckError, channel_name,
    fetch_releases, repository_slug, select_release, version_comparison,
)


def release(tag, date, *, prerelease=False, draft=False, assets=None):
    return {
        "tag_name": tag, "published_at": date, "draft": draft,
        "prerelease": prerelease, "body": "Changes in " + tag,
        "assets": assets or [],
    }


def response(request, payload, *, mime="application/json", status=200):
    return httpx.Response(status, headers={"Content-Type": mime},
                          stream=httpx.ByteStream(payload), request=request)


def test_channel_and_draft_filter_with_dates_not_input_order():
    rows = [
        release("v1.0", "2026-06-01T12:00:00Z"),
        release("v1.2", "2026-09-01T12:00:00Z"),
        release("v2.0rc1", "2026-10-01T12:00:00Z", prerelease=True),
        release("v9.0", "2026-11-01T12:00:00Z", draft=True),
    ]
    assert select_release(rows, channel="stable").tag == "v1.2"
    assert select_release(rows, channel="prerelease").tag == "v2.0rc1"
    assert select_release(rows, channel="dev").tag == "v2.0rc1"
    assert select_release([], channel="stable") is None


@pytest.mark.parametrize("value", [
    "", "owner", "owner//repo", "../repo", "owner/../repo", "https://evil/path",
    "owner/repo/extra", "owner/repo?token=x", "owner/repo#frag", "owner:123/repo",
])
def test_repository_validation_prevents_arbitrary_http_targets(value):
    with pytest.raises(ReleaseCheckError):
        repository_slug(value)


def test_url_allowlist_no_auth_and_single_explicit_get():
    calls = []
    body = json.dumps([release(
        "v1.1", "2026-10-01T12:00:00Z",
        assets=[
            {"name": "v1.1.zip"},
            {"name": "v1.1.zip.sha256"},
            {"name": "v1.1.zip.sigstore.json"},
            {"name": "malicious space.zip"},
            {"name": "note.pdf"},
        ],
    )]).encode()
    def opener(req):
        calls.append(req)
        return response(req, body)
    chosen = fetch_releases(DEFAULT_REPOSITORY, transport=httpx.MockTransport(opener))
    assert chosen.tag == "v1.1"
    assert chosen.zip_names == ("v1.1.zip",)
    assert chosen.verification_names == ("v1.1.zip.sha256", "v1.1.zip.sigstore.json")
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert str(calls[0].url) == (
        "https://api.github.com/repos/pzia/cifereca/releases?per_page=30"
    )
    assert not any("authorization" in key.casefold() for key in calls[0].headers)


@pytest.mark.parametrize("options", [
    {"mime": "text/html"},
    {"status": 301},
    {"payload": b"not-json"},
    {"payload": b"{}"},
    {"payload": b"[" + b" " * MAX_RESPONSE + b"]"},
])
def test_bad_response_is_rejected_without_payload_leak(options):
    url = "https://api.github.com/repos/pzia/cifereca/releases?per_page=30"
    body = options.pop("payload", b"[]")
    with pytest.raises(ReleaseCheckError):
        fetch_releases(transport=httpx.MockTransport(lambda req: response(req, body, **options)))


def test_redirect_and_http_errors_are_friendly_and_do_not_expose_url():
    requests=[]
    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location":"https://evil.invalid/release"})
    with pytest.raises(ReleaseCheckError, match="redirigée"):
        fetch_releases(transport=httpx.MockTransport(redirect))
    assert len(requests)==1
    with pytest.raises(ReleaseCheckError, match="limite"):
        fetch_releases(transport=httpx.MockTransport(lambda r:httpx.Response(403)))
    def offline(request):
        raise httpx.ConnectError("secret-host or token",request=request)
    with pytest.raises(ReleaseCheckError) as caught:
        fetch_releases(transport=httpx.MockTransport(offline))
    assert "secret" not in str(caught.value)


def test_invalid_data_in_drafts_and_assets_is_not_trusted():
    rows = [
        release("hidden", "2026-10-01T12:00:00Z", draft=True),
        release("invalid", "not-a-date"),
        release("published", "2026-09-01T00:00:00Z", assets=[
            {"name": "../../steal.zip"},
            {"name": "good.zip"},
            {"name": "file.exe"},
            "not an asset",
        ]),
    ]
    chosen = select_release(rows, channel="stable")
    assert chosen.tag == "published"
    assert chosen.zip_names == ("good.zip",)
    with pytest.raises(ReleaseCheckError):
        select_release({"message": "Bad credentials"}, channel="stable")


def test_version_comparison_does_not_invent_a_new_version():
    assert "plus récente" in version_comparison("1.0.0", "v1.1.0")
    assert "identique" in version_comparison("1.0.0", "1.0.0")
    assert "antérieure" in version_comparison("1.0.0", "0.9.0")
    assert "non comparables" in version_comparison("1.0.0", "release-unknown")
    assert "indisponible" in version_comparison(None, "v1.0")
    with pytest.raises(ReleaseCheckError):
        channel_name("nightly")
