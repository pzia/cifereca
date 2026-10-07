from __future__ import annotations

from cifereca.plugin_api import HealthCheckResult


class HttpTransportHealthChecks:
    def __init__(self) -> None:
        self.transport = None

    def activate(self, context):
        self.transport = context.service("transport.http")
        if self.transport is None:
            raise RuntimeError("transport.http indisponible")
        return self

    def checks(self) -> tuple[HealthCheckResult, ...]:
        transport = self.transport
        if transport is None:
            return (
                HealthCheckResult(
                    "transport.http.configuration",
                    "transport.http",
                    "error",
                    "Transport HTTP",
                    {"available": False},
                    "transport",
                ),
            )

        try:
            timeout = float(getattr(transport, "timeout_seconds"))
            maximum = int(getattr(transport, "max_download_bytes"))
            redirects = int(getattr(transport, "max_redirects"))
        except Exception as exc:
            return (
                HealthCheckResult(
                    "transport.http.configuration",
                    "transport.http",
                    "error",
                    "Transport HTTP",
                    {"available": True, "error_type": type(exc).__name__},
                    "transport",
                ),
            )

        sane = timeout >= 1 and maximum >= 1 and redirects >= 0
        return (
            HealthCheckResult(
                "transport.http.configuration",
                "transport.http",
                "ok" if sane else "error",
                "Transport HTTP",
                {
                    "available": True,
                    "timeout_seconds": timeout,
                    "max_download_bytes": maximum,
                    "max_redirects": redirects,
                },
                "transport",
            ),
        )

    def deactivate(self) -> None:
        self.transport = None


PLUGIN = HttpTransportHealthChecks()
