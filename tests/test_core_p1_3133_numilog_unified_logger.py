"""#3133: the independent Numilog module logger shares the one physical log."""
from __future__ import annotations

import logging
import stat

from cifereca import logging_utils
from cifereca.core.diagnostic_journal import query_journal


def test_numilog_and_core_share_one_file_rotation_and_trace_console(tmp_path, monkeypatch):
    path = tmp_path / "cifereca.log"
    monkeypatch.setattr(logging_utils, "log_file", lambda: path)
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    core = logging.getLogger("cifereca")
    numilog = logging.getLogger("cifereca_numilog")
    old_core = (tuple(core.handlers), core.level, core.propagate)
    old_numilog = (tuple(numilog.handlers), numilog.level, numilog.propagate)
    try:
        for handler in tuple(core.handlers):
            core.removeHandler(handler)
        for handler in tuple(numilog.handlers):
            numilog.removeHandler(handler)

        logging_utils.configure_logging()
        assert len(core.handlers) == 1
        assert numilog.handlers == core.handlers
        assert numilog.propagate is False
        numilog.warning("Numilog prêt provider_id=numilog token=visible")
        core.warning("Core prêt provider_id=paris")
        logging_utils.set_diagnostics_level("trace")
        assert numilog.level == core.level == logging.DEBUG
        numilog.debug("TRACE numilog session status=ready phase=completed")
        assert numilog.handlers == core.handlers  # repeat configuration is idempotent
        logging_utils.configure_logging()
        assert numilog.handlers == core.handlers
    finally:
        created = {id(handler): handler for handler in (*core.handlers, *numilog.handlers)}
        for handler in tuple(core.handlers):
            core.removeHandler(handler)
        for handler in tuple(numilog.handlers):
            numilog.removeHandler(handler)
        for handler in created.values():
            if handler not in old_core[0] and handler not in old_numilog[0]:
                handler.close()
        for handler in old_core[0]:
            core.addHandler(handler)
        for handler in old_numilog[0]:
            numilog.addHandler(handler)
        core.setLevel(old_core[1])
        core.propagate = old_core[2]
        numilog.setLevel(old_numilog[1])
        numilog.propagate = old_numilog[2]

    result = query_journal(path)
    bodies = [row["message"] for row in result["events"]]
    assert len(bodies) == 3
    assert sum("Numilog prêt" in body for body in bodies) == 1
    assert sum("Core prêt" in body for body in bodies) == 1
    assert any("TRACE numilog session" in body for body in bodies)
    assert all("token=visible" in body for body in bodies if "Numilog prêt" in body)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
