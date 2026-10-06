from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from cifereca.plugin_api import RequirementStatus, SystemRequirement

_VERSION_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+){0,3}(?:[-+._a-zA-Z0-9]*)?)")


def _matches(version: str, spec: str) -> bool:
    if not spec:
        return True
    try:
        return Version(version) in SpecifierSet(spec)
    except InvalidVersion:
        return False


def _binary_version(executable: str, args: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(
            [executable, *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=5,
        )
    except Exception:
        return ""
    match = _VERSION_RE.search(result.stdout or "")
    return match.group(1) if match else ""


def check_system_requirement(requirement: SystemRequirement) -> RequirementStatus:
    kind = requirement.type
    if kind == "binary":
        executable = shutil.which(requirement.name)
        if not executable:
            return RequirementStatus(requirement, False, message=f"binary not found: {requirement.name}")
        detected = _binary_version(executable, requirement.version_args) if requirement.version else executable
        if requirement.version and (not detected or not _matches(detected, requirement.version)):
            return RequirementStatus(
                requirement, False, detected=detected,
                message=f"{requirement.name} {detected or 'unknown'} does not satisfy {requirement.version}",
            )
        return RequirementStatus(requirement, True, detected=detected or executable)
    if kind == "file":
        path = Path(requirement.path or requirement.name).expanduser()
        return RequirementStatus(requirement, path.is_file(), detected=str(path), message="" if path.is_file() else "file not found")
    if kind == "directory":
        path = Path(requirement.path or requirement.name).expanduser()
        return RequirementStatus(requirement, path.is_dir(), detected=str(path), message="" if path.is_dir() else "directory not found")
    if kind == "os":
        current = platform.system().lower()
        wanted = requirement.name.strip().lower()
        aliases = {"linux": "linux", "windows": "windows", "darwin": "darwin", "macos": "darwin"}
        ok = current == aliases.get(wanted, wanted)
        return RequirementStatus(requirement, ok, detected=current, message="" if ok else f"requires OS {requirement.name}")
    if kind == "python":
        detected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ok = _matches(detected, requirement.version) if requirement.version else True
        return RequirementStatus(requirement, ok, detected=detected, message="" if ok else f"Python {detected} does not satisfy {requirement.version}")
    if kind == "python-package":
        try:
            detected = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            return RequirementStatus(requirement, False, message=f"Python distribution not installed: {requirement.name}")
        except Exception as exc:
            return RequirementStatus(requirement, False, message=f"cannot inspect Python distribution {requirement.name}: {exc}")
        if requirement.version and not _matches(detected, requirement.version):
            return RequirementStatus(
                requirement, False, detected=detected,
                message=f"{requirement.name} {detected} does not satisfy {requirement.version}",
            )
        if requirement.import_name and importlib.util.find_spec(requirement.import_name) is None:
            return RequirementStatus(
                requirement, False, detected=detected,
                message=f"Python import not found for {requirement.name}: {requirement.import_name}",
            )
        return RequirementStatus(requirement, True, detected=detected)
    return RequirementStatus(requirement, False, message=f"unsupported requirement type: {kind}")


def check_system_requirements(requirements: tuple[SystemRequirement, ...]) -> tuple[RequirementStatus, ...]:
    return tuple(check_system_requirement(item) for item in requirements)
