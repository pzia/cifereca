"""Minimal physical log-line parser shared by the canonical journal and legacy tests.

No dependency on Diagnostics, providers, a memory snapshot or network code.
"""
from __future__ import annotations

import re

_LOG_LINE = re.compile(
    r"^(?P<date>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<logger>[\w.-]{1,90}) (?:\[[^\]]{0,80}\] )?(?P<body>.*)$"
)
