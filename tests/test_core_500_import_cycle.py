from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_plugin_api_import_does_not_initialize_core_runtime_or_plugins():
    result = _run(
        "import sys\n"
        "import cifereca.plugin_api\n"
        "assert 'cifereca.core.runtime' not in sys.modules\n"
        "assert 'cifereca.plugins' not in sys.modules\n"
    )
    assert result.returncode == 0, result.stderr


def test_core_public_exports_remain_available_lazily():
    result = _run(
        "from cifereca.core import CiferecaCore, LocalLibrarySearch, MaterializationCoordinator, MaterializationResult\n"
        "assert CiferecaCore.__name__ == 'CiferecaCore'\n"
        "assert LocalLibrarySearch.__name__ == 'LocalLibrarySearch'\n"
        "assert MaterializationCoordinator.__name__ == 'MaterializationCoordinator'\n"
        "assert MaterializationResult.__name__ == 'MaterializationResult'\n"
    )
    assert result.returncode == 0, result.stderr
