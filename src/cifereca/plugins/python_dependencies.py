from __future__ import annotations

import importlib.metadata
import json
from datetime import datetime, timezone
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from packaging.utils import canonicalize_name

from cifereca.plugin_api import SystemRequirement

from .errors import PackageInstallError
from .python_quarantine import QuarantineRejected, check_pip_report
from .manifest import PluginPackageManifest
from .requirements import check_system_requirement


class PythonDependencyError(PackageInstallError):
    """Raised when Core cannot satisfy a plugin Python distribution requirement."""


def is_python_package_requirement(requirement: SystemRequirement) -> bool:
    return requirement.type == "python-package"


def _requirement_text(requirement: SystemRequirement) -> str:
    name = requirement.name.strip()
    if not name:
        raise PythonDependencyError("python-package requirement requires a distribution name")
    return name + (requirement.version or "")


def _installed_versions() -> dict[str, tuple[str, str]]:
    """Return canonical-name -> (display-name, version) for the current interpreter."""
    out: dict[str, tuple[str, str]] = {}
    for dist in importlib.metadata.distributions():
        try:
            name = str(dist.metadata.get("Name") or "").strip()
            version = str(dist.version or "").strip()
        except Exception:
            continue
        if name and version:
            out[canonicalize_name(name)] = (name, version)
    return out


def _running_in_venv() -> bool:
    return bool(os.environ.get("VIRTUAL_ENV")) or sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _core_wheelhouse() -> Path | None:
    candidates = [
        Path(sys.prefix).resolve().parent / "offline-wheels",
        Path(__file__).resolve().parents[3] / "offline-wheels",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


@dataclass(slots=True)
class PythonDependencyTransaction:
    manager: "PythonDependencyManager"
    before: dict[str, tuple[str, str]]
    wheel_dirs: tuple[Path, ...]
    changed: bool = False
    _closed: bool = False

    def commit(self) -> None:
        self._closed = True

    def rollback(self) -> None:
        if self._closed or not self.changed:
            self._closed = True
            return
        self.manager._restore(self.before, self.wheel_dirs)
        self._closed = True


class PythonDependencyManager:
    """Core-owned resolver/installer for Python dependencies declared by plugins.

    Plugins never invoke pip. The Core aggregates constraints from active package
    manifests and, when installation is explicitly requested, resolves them in the
    Cifereca runtime venv before any plugin entrypoint is imported.
    """

    def __init__(self, *, allow_index: bool | None = None) -> None:
        if allow_index is None:
            allow_index = os.environ.get("CIFERECA_PLUGIN_DEPS_OFFLINE", "").strip().lower() not in {
                "1", "true", "yes", "on",
            }
        self.allow_index = bool(allow_index)

    @staticmethod
    def requirements(manifests: Iterable[PluginPackageManifest]) -> tuple[SystemRequirement, ...]:
        seen: set[tuple[str, str, str]] = set()
        rows: list[SystemRequirement] = []
        for manifest in manifests:
            for descriptor in manifest.plugins:
                for requirement in descriptor.system_requires:
                    if not is_python_package_requirement(requirement):
                        continue
                    key = (canonicalize_name(requirement.name), requirement.version, requirement.import_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(requirement)
        return tuple(rows)

    @staticmethod
    def wheel_dirs(package_roots: Iterable[Path]) -> tuple[Path, ...]:
        out: list[Path] = []
        core = _core_wheelhouse()
        if core is not None:
            out.append(core)
        for root in package_roots:
            for relative in ("wheels", "wheelhouse"):
                candidate = Path(root) / relative
                if candidate.is_dir() and candidate not in out:
                    out.append(candidate)
        return tuple(out)

    def ensure(
        self,
        manifests: Sequence[PluginPackageManifest],
        *,
        package_roots: Iterable[Path] = (),
    ) -> PythonDependencyTransaction:
        requirements = self.requirements(manifests)
        wheel_dirs = self.wheel_dirs(package_roots)
        before = _installed_versions()
        transaction = PythonDependencyTransaction(self, before, wheel_dirs)
        if not requirements:
            return transaction

        failed = [check_system_requirement(item) for item in requirements]
        failed = [item for item in failed if not item.ok]
        if not failed:
            return transaction
        if not _running_in_venv():
            missing = ", ".join(_requirement_text(item.requirement) for item in failed)
            raise PythonDependencyError(
                "Core refuses to modify Python outside the Cifereca virtual environment; "
                f"unsatisfied plugin dependencies: {missing}"
            )

        requested = tuple(dict.fromkeys(_requirement_text(item) for item in requirements))
        try:
            self._pip_install(requested, wheel_dirs)
            transaction.changed = _installed_versions() != before
            verify = [check_system_requirement(item) for item in requirements]
            verify_failed = [item for item in verify if not item.ok]
            if verify_failed:
                detail = "; ".join(item.message for item in verify_failed)
                raise PythonDependencyError(f"Python plugin dependencies remain unsatisfied after pip: {detail}")
            return transaction
        except Exception as exc:
            # pip is normally transactional, but restore the exact pre-operation
            # environment on a partial resolver/install failure whenever possible.
            try:
                if _installed_versions() != before:
                    self._restore(before, wheel_dirs)
            except Exception as restore_exc:
                raise PythonDependencyError(
                    f"Python dependency installation failed and venv rollback also failed: {restore_exc}"
                ) from exc
            if isinstance(exc, PythonDependencyError):
                raise
            # Provider/import errors can contain arbitrary credentials, paths,
            # or signed URLs. Preserve structured quarantine errors above,
            # redact every other unexpected dependency failure.
            raise PythonDependencyError("dependency_provenance_unavailable") from None

    def _pip_base(self, wheel_dirs: Sequence[Path]) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade-strategy",
            "only-if-needed",
        ]
        for directory in wheel_dirs:
            command.extend(["--find-links", str(directory)])
        if not self.allow_index:
            command.append("--no-index")
        return command

    def _pip_install(self, requested: Sequence[str], wheel_dirs: Sequence[Path]) -> None:
        if not requested:
            return
        # P0 #2770: pip is still the standard resolver. Its dry-run plan
        # includes transitive dependencies. No venv writes are allowed before
        # *each exact proposed wheel* has a PyPI release-time + SHA proof.
        # Disallow an unattested local wheelhouse/index as a silent fallback.
        if not self.allow_index:
            raise PythonDependencyError("dependency_provenance_unavailable")
        with tempfile.NamedTemporaryFile(prefix="cifereca-pip-", suffix=".json", delete=False) as report:
            report_path = Path(report.name)
        try:
            # pip itself finds an older compatible release when a selected
            # wheel is quarantined. Bounded --constraint retries only; never
            # implement our own dependency traversal or blindly downgrade.
            constraints: dict[str, str] = {}
            verified = None
            for _attempt in range(12):
                resolver = [
                    sys.executable, "-m", "pip", "--isolated", "install",
                    "--disable-pip-version-check", "--dry-run", "--report",
                    str(report_path), "--no-cache-dir", "--only-binary=:all:",
                    "--index-url", "https://pypi.org/simple", *requested,
                ]
                if constraints:
                    constraint_path = report_path.with_suffix(".constraints.txt")
                    constraint_path.write_text(
                        "".join(f"{name}<{version}\n" for name, version in sorted(constraints.items())),
                        encoding="utf-8",
                    )
                    resolver += ["--constraint", str(constraint_path)]
                proc = subprocess.run(resolver, text=True, capture_output=True, timeout=300, check=False)
                if proc.returncode != 0:
                    raise PythonDependencyError("dependency_not_eligible")
                try:
                    plan = json.loads(report_path.read_text(encoding="utf-8"))
                    verified = check_pip_report(plan, now=datetime.now(timezone.utc))
                    break
                except QuarantineRejected as exc:
                    # A missing proof, mismatch or yanked package must NEVER
                    # be treated as an excuse to select an arbitrary fallback.
                    if exc.args != ("dependency_too_recent",) or not exc.name or not exc.version:
                        raise PythonDependencyError(str(exc)) from None
                    if exc.name in constraints:
                        from packaging.version import InvalidVersion, Version
                        try:
                            if Version(exc.version) >= Version(constraints[exc.name]):
                                raise PythonDependencyError("dependency_not_eligible") from None
                        except InvalidVersion:
                            raise PythonDependencyError("dependency_not_eligible") from None
                    constraints[exc.name] = exc.version
                except (OSError, ValueError, TypeError):
                    raise PythonDependencyError("dependency_provenance_unavailable") from None
            if verified is None:
                raise PythonDependencyError("dependency_not_eligible")
            if not verified:
                return  # All requested constraints were already satisfied.
            # SHA-fragment URLs retain the EXACT attested wheel choice, while
            # --no-deps and --no-index prevent a second unlocked resolution.
            install = [
                sys.executable, "-m", "pip", "--isolated", "install",
                "--disable-pip-version-check", "--no-index", "--no-deps",
                "--no-cache-dir", *verified,
            ]
            proc = subprocess.run(install, text=True, capture_output=True, timeout=300, check=False)
            if proc.returncode != 0:
                raise PythonDependencyError("dependency_not_eligible")
        finally:
            report_path.unlink(missing_ok=True)
            report_path.with_suffix(".constraints.txt").unlink(missing_ok=True)

    def _restore(self, before: dict[str, tuple[str, str]], wheel_dirs: Sequence[Path]) -> None:
        after = _installed_versions()
        added = [display for key, (display, _version) in after.items() if key not in before]
        changed = [
            (before[key][0], before[key][1])
            for key, (_display, version) in after.items()
            if key in before and version != before[key][1]
        ]
        if added:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "-y", *sorted(set(added))],
                text=True, capture_output=True, timeout=300, check=False,
            )
            if proc.returncode != 0:
                # pip may echo private indexes, credentials or user paths.
                # Diagnostics and CLI must never echo subprocess output.
                raise PythonDependencyError("dependency_not_eligible")
        if changed:
            # Rollback is another third-party wheel acquisition. Never bypass
            # the exact-file PyPI SHA/date preflight by using a raw pip command
            # or an unverified cached/offline wheelhouse (#2770).
            requested = [f"{name}=={version}" for name, version in changed]
            self._pip_install(requested, wheel_dirs)

    def diagnostic(self, manifests: Sequence[PluginPackageManifest]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for requirement in self.requirements(manifests):
            status = check_system_requirement(requirement)
            rows.append({
                "distribution": requirement.name,
                "version": requirement.version,
                "import": requirement.import_name,
                "ok": status.ok,
                "detected": status.detected,
                "message": status.message,
            })
        return rows
