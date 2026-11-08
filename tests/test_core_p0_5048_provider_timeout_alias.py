"""P0 #5048: timeout option aliases are provider/account-neutral and scoped."""
import pytest

from cifereca.config import Config


@pytest.mark.parametrize("provider", ["kobo", "numilog", "future-store"])
def test_timeout_alias_respects_exact_provider_and_credential(tmp_path, provider):
    config = Config(tmp_path / "settings.json")
    config.set("request_timeout_seconds", 27)
    config.set("providers.accounts", [
        {"id": "one", "provider": provider, "identity_id": "alice",
         "options": {"timeout_seconds": 13}},
        {"id": "two", "provider": provider, "identity_id": "bob",
         "options": {"request_timeout_seconds": 31, "timeout_seconds": 8}},
        {"id": "other", "provider": "unrelated-store", "identity_id": "bob",
         "options": {"timeout_seconds": 99}},
    ])
    assert config.request_timeout_seconds(provider, "one") == 13.0
    # The canonical option always wins over the compatibility alias.
    assert config.request_timeout_seconds(provider, "two") == 31.0
    assert config.request_timeout_seconds(provider, "missing") == 27.0
    assert config.request_timeout_seconds("missing-provider", "one") == 27.0
    assert config.request_timeout_seconds("unrelated-store", "other") == 99.0


def test_invalid_alias_is_not_reinterpreted_as_a_permission_or_global_timeout(tmp_path):
    config = Config(tmp_path / "settings.json")
    config.set("request_timeout_seconds", 27)
    config.set("providers.accounts", [
        {"id": "one", "provider": "future-store",
         "options": {"timeout_seconds": "not-a-number"}},
        {"id": "two", "provider": "future-store", "options": {}},
    ])
    assert config.request_timeout_seconds("future-store", "one") != 27.0
    assert config.request_timeout_seconds("future-store", "two") == 27.0
