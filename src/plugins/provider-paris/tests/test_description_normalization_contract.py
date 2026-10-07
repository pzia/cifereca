from cifereca.plugin_api import description_text
from cifereca_paris_normalize import normalize_search_result


def test_paris_uses_public_description_normalization():
    raw = {
        "Resource": {
            "RscId": "1",
            "RscBase": "SYRACUSE",
            "Ttl": "Titre",
            "Desc": "<p>Premier</p><script>secret()</script><p>Second</p>",
        }
    }
    row = normalize_search_result(raw, "search")
    assert row is not None
    assert row.description == "Premier\n\nSecond"
    assert description_text("<p>Premier</p><p>Second</p>") == row.description
