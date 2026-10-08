from __future__ import annotations

import httpx
import pytest

from cifereca_paris_client import ParisClient, ParisError


class RecordingDiagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


class PairingHttp:
    def __init__(self, failures=()):
        self.failures = set(failures)

    def get(self, _url, *, params, headers):
        provider = params["serviceCode"]
        if provider in self.failures:
            raise RuntimeError(f"{provider} unavailable")
        return httpx.Response(
            200,
            json={
                "success": True,
                "d": {
                    "UserPairings": [{
                        "State": 2,
                        "UserUniqueIdentifier": f"{provider.lower()}-linked",
                        "UserDisplayName": f"{provider} lié",
                        "UserBarcode": provider,
                    }],
                    "UserReversePairings": [],
                },
            },
        )

    def close(self):
        return None


@pytest.mark.parametrize(
    ("failures", "expected_id", "failed_upstream"),
    [
        ({"DILICOM"}, "syracuse-linked", "DILICOM"),
        ({"SYRACUSE"}, "dilicom-linked", "SYRACUSE"),
    ],
)
def test_pairings_keep_healthy_backend_when_other_fails(
    failures, expected_id, failed_upstream
):
    diagnostics = RecordingDiagnostics()
    http = PairingHttp(failures)
    client = ParisClient(
        diagnostics=diagnostics,
        client_factory=lambda: http,
    )

    try:
        rows = client.pairings()

        assert [row.unique_id for row in rows] == [expected_id]
        assert client.last_pairing_errors == [{
            "upstream": failed_upstream,
            "type": "RuntimeError",
        }]
        assert any(
            args[:3] == ("API", f"pairings {failed_upstream}", "error")
            and kwargs.get("detail") == {
                "upstream": failed_upstream,
                "type": "RuntimeError",
            }
            for args, kwargs in diagnostics.rows
        )
    finally:
        client.close()


def test_pairings_raise_only_when_both_backends_fail():
    client = ParisClient(
        diagnostics=RecordingDiagnostics(),
        client_factory=lambda: PairingHttp({"SYRACUSE", "DILICOM"}),
    )

    try:
        with pytest.raises(ParisError, match="pairings indisponibles"):
            client.pairings()

        assert client.last_pairing_errors == [
            {"upstream": "SYRACUSE", "type": "RuntimeError"},
            {"upstream": "DILICOM", "type": "RuntimeError"},
        ]
    finally:
        client.close()
