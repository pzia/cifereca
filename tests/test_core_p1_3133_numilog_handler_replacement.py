"""#3133: obsolete Numilog compatibility handlers never become a second journal.

Local files only; no Numilog account, network, plugin activation or credentials.
"""
from __future__ import annotations

import logging

from cifereca import logging_utils
from cifereca.core.diagnostic_journal import query_journal


def _handler(path):
    handler = logging_utils.PrivateRotatingFileHandler(
        path, maxBytes=100_000, backupCount=1, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s",
    ))
    return handler


def test_reconfigured_core_handlers_replace_old_numilog_file_not_append(tmp_path):
    core = logging.getLogger("cifereca")
    numilog = logging.getLogger("cifereca_numilog")
    initial_core = tuple(core.handlers), core.level, core.propagate
    initial_numilog = tuple(numilog.handlers), numilog.level, numilog.propagate
    old_path = tmp_path / "old-cifereca.log"
    new_path = tmp_path / "new-cifereca.log"
    old = _handler(old_path)
    new = _handler(new_path)
    try:
        for h in tuple(core.handlers):
            core.removeHandler(h)
        for h in tuple(numilog.handlers):
            numilog.removeHandler(h)
        core.setLevel(logging.DEBUG)

        core.addHandler(old)
        logging_utils._share_canonical_handlers(core)
        assert numilog.handlers == [old]
        numilog.warning("phase-one only in previous journal")

        # Simulate a complete Core reconfiguration in the same process.
        core.removeHandler(old)
        core.addHandler(new)
        logging_utils._share_canonical_handlers(core)
        logging_utils._share_canonical_handlers(core)  # Idempotence.
        assert numilog.handlers == [new]
        numilog.warning("phase-two only in current journal")
        core.warning("phase-three current Core logger")

        # A caller may remove all Core handlers before replacing the journal.
        core.removeHandler(new)
        logging_utils._share_canonical_handlers(core)
        assert numilog.handlers == []
    finally:
        for h in tuple(core.handlers):
            core.removeHandler(h)
        for h in tuple(numilog.handlers):
            numilog.removeHandler(h)
        old.close()
        new.close()
        for h in initial_core[0]:
            core.addHandler(h)
        for h in initial_numilog[0]:
            numilog.addHandler(h)
        core.setLevel(initial_core[1])
        core.propagate = initial_core[2]
        numilog.setLevel(initial_numilog[1])
        numilog.propagate = initial_numilog[2]

    older = query_journal(old_path)
    current = query_journal(new_path)
    assert [row["message"] for row in older["events"]] == [
        "phase-one only in previous journal",
    ]
    assert [row["message"] for row in current["events"]] == [
        "phase-two only in current journal",
        "phase-three current Core logger",
    ]
    assert len({row["event_id"] for row in current["events"]}) == 2
