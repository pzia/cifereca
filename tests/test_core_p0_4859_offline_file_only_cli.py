"""P0 #4859: deprecated Kobo byte-only CLI is no longer registered."""
from __future__ import annotations

from unittest.mock import Mock
import pytest
from cifereca import cli


def test_observe_local_command_cannot_start_core(monkeypatch, capsys):
    opener = Mock(side_effect=AssertionError("must not start Core"))
    monkeypatch.setattr(cli.CiferecaCore, "open", opener)
    with pytest.raises(SystemExit) as rejected:
        cli.main(["kobo-observe-local"])
    assert rejected.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    opener.assert_not_called()
