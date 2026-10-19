"""Optional public host adapter; no collector, implicit capture or file I/O."""
from __future__ import annotations

import json


def performance_bytes(report):
    if (type(report) is not dict
            or report.get("schema") != "cifereca.performance.phases/1"
            or type(report.get("detailed_capture_enabled")) is not bool
            or report.get("remote_upload") is not False):
        raise ValueError("performance_report_unavailable")
    # UiPerformanceHost owns this already bounded/redacted Core schema.
    # Preserve its measurements exactly; never cut a JSON document in half.
    data = json.dumps(report, ensure_ascii=False, allow_nan=False,
                      indent=2).encode("utf-8")
    if len(data) > 1_000_000:
        raise ValueError("performance_report_too_large")
    return data


class PerformanceCapture:
    def __init__(self, host):
        self.host = host

    def read(self):
        reader = getattr(self.host, "performance_report", None)
        if not callable(reader):
            raise ValueError("performance_host_unavailable")
        # A JSON roundtrip detaches the view from the shared monitor.
        return json.loads(performance_bytes(reader()))

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("invalid_capture_option")
        setter = getattr(self.host, "set_performance_capture", None)
        if not callable(setter):
            raise ValueError("performance_host_unavailable")
        return json.loads(performance_bytes(setter(enabled)))
