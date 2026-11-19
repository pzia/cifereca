"""#4496: isolated development/test opt-in, safe persisted defaults."""
import json

import pytest

from cifereca.config import Config
from cifereca.core.destructive_cleanup_policy import (
    DESTRUCTIVE_CLEANUP_KEY as KEY, destructive_cleanup_enabled as enabled,
)


def test_default_and_persisted_test_opt_in(tmp_path):
    path = tmp_path / "config.json"
    config = Config(path)
    assert config.get(KEY) is False
    assert not enabled(config)
    # Development/recette owns an isolated configuration and explicitly
    # sets true, never deriving permission from a build type or env var.
    config.set(KEY, True)
    config.save()
    loaded = Config(path)
    assert enabled(loaded)
    assert json.loads(path.read_text())["maintenance"]["destructive_cleanup_enabled"] is True
    loaded.set(KEY, False)
    loaded.save()
    assert not enabled(loaded)
    assert not enabled(Config(path))


@pytest.mark.parametrize("value", [None, False, "true", "false", 1, 0, [], {}])
def test_invalid_or_false_values_never_authorize(tmp_path, value):
    config = Config(tmp_path / "config.json")
    config.set(KEY, value)
    config.save()
    assert not enabled(Config(config.path))


def test_malformed_and_old_config_fail_closed(tmp_path):
    path = tmp_path / "config.json"
    for raw in ('{}', '{"maintenance":true}', '{"maintenance":{}}', 'invalid JSON'):
        path.write_text(raw)
        assert not enabled(Config(path))


def test_environment_and_untrusted_mapping_are_not_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("CIFERECA_DESTRUCTIVE_CLEANUP_ENABLED", "true")
    assert not enabled(Config(tmp_path / "config.json"))
    assert not enabled(None)
    assert not enabled({KEY: True})
