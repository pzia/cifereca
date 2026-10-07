from __future__ import annotations

from collections.abc import Mapping

from cifereca.plugin_api import HealthCheckResult


class KoboAdeptHealthChecks:
    def __init__(self) -> None:
        self.runtime = None

    def activate(self, context):
        self.runtime = context.service("runtime.kobo-adept")
        if self.runtime is None:
            raise RuntimeError("runtime.kobo-adept indisponible")
        return self

    def checks(self) -> tuple[HealthCheckResult, ...]:
        runtime = self.runtime
        if runtime is None:
            return (
                HealthCheckResult(
                    "runtime.kobo-adept",
                    "runtime.kobo-adept",
                    "error",
                    "Kobo-Adept",
                    {"available": False},
                ),
            )

        try:
            available = bool(runtime.available())
        except Exception as exc:
            return (
                HealthCheckResult(
                    "runtime.kobo-adept",
                    "runtime.kobo-adept",
                    "error",
                    "Kobo-Adept",
                    {"available": False, "error_type": type(exc).__name__},
                ),
            )
        if not available:
            return (
                HealthCheckResult(
                    "runtime.kobo-adept",
                    "runtime.kobo-adept",
                    "error",
                    "Kobo-Adept",
                    {"available": False},
                ),
            )

        try:
            payload = runtime.probe()
        except Exception as exc:
            return (
                HealthCheckResult(
                    "runtime.kobo-adept",
                    "runtime.kobo-adept",
                    "error",
                    "Kobo-Adept",
                    {"available": True, "error_type": type(exc).__name__},
                ),
            )

        contract_version = None
        resolved_executable = False
        if isinstance(payload, Mapping):
            contract = payload.get("contract")
            if isinstance(contract, Mapping):
                contract_version = contract.get("contract_version") or contract.get("version")
            resolved_executable = bool(payload.get("resolved_executable"))

        return (
            HealthCheckResult(
                "runtime.kobo-adept",
                "runtime.kobo-adept",
                "ok" if resolved_executable else "warning",
                "Kobo-Adept",
                {
                    "available": True,
                    "contract_version": contract_version,
                    "resolved_executable": resolved_executable,
                },
            ),
        )

    def deactivate(self) -> None:
        self.runtime = None


PLUGIN = KoboAdeptHealthChecks()
