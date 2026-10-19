from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

from cifereca_kobo_adept_runtime import KoboAdeptRuntime


FAKE_CLI = r"""#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

args = sys.argv[1:]
if len(args) < 3 or args[0] != "--state":
    print(json.dumps({"ok": False, "error": {"code": "bad_test_cli", "message": "state missing"}}))
    raise SystemExit(2)

state_path = Path(args[1])
argv = args[2:]
if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
else:
    state = {"profiles": {}, "calls": []}
state.setdefault("profiles", {})
state.setdefault("calls", []).append(argv)

def save():
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def profile_payload(name):
    row = state["profiles"].get(name)
    if row is None:
        return None
    return {
        "name": name,
        "complete": True,
        "fetch_capable": True,
        "devices": list(row.get("devices", [])),
        "provisioning": {
            "state": "ready",
            "adept_identity_available": True,
            "device_linked": bool(row.get("devices")),
            "reader_backed_identity_ready": True,
            "acsm_fetch_ready": True,
        },
    }

if argv == ["contract", "--json"]:
    save()
    print(json.dumps({
        "name": "kobo-adept",
        "version": "0.13.6",
        "contract_version": 8,
        "capabilities": ["strict_json_stdout"],
    }))
elif argv == ["capabilities", "--json"]:
    save()
    print(json.dumps({
        "ok": True,
        "capabilities": {
            "adept": {
                "profile_provisioning": {"available": True},
                "loan_return": {"available": True},
                "fetch_capable_profiles": sorted(state["profiles"]),
            },
            "download": {
                "acsm": {"available": True},
                "http": {"available": True},
                "kobo": {"available": False},
            },
        },
    }))
elif argv == ["profile", "list", "--json"]:
    save()
    print(json.dumps({
        "ok": True,
        "profiles": [profile_payload(name) for name in sorted(state["profiles"])],
    }))
elif len(argv) >= 4 and argv[:2] == ["profile", "add"] and "--username" in argv and argv[-1] == "--json":
    name = argv[2]
    username = argv[argv.index("--username") + 1]
    state["profiles"][name] = {"username": username, "devices": []}
    save()
    print(json.dumps({"ok": True, "profile": profile_payload(name)}))
elif len(argv) == 4 and argv[:2] == ["profile", "show"] and argv[-1] == "--json":
    name = argv[2]
    payload = profile_payload(name)
    save()
    if payload is None:
        print(json.dumps({"ok": False, "error": {"code": "profile_missing", "message": "profil absent"}}))
        raise SystemExit(1)
    print(json.dumps({"ok": True, "profile": payload}))
elif len(argv) >= 5 and argv[:2] == ["profile", "associate"] and "--mount" in argv and argv[-1] == "--json":
    name = argv[2]
    mount = argv[argv.index("--mount") + 1]
    if name not in state["profiles"]:
        print(json.dumps({"ok": False, "error": {"code": "profile_missing", "message": "profil absent"}}))
        raise SystemExit(1)
    state["profiles"][name].setdefault("devices", []).append({"mount": mount})
    save()
    print(json.dumps({"ok": True, "profile": profile_payload(name)}))
elif len(argv) == 4 and argv[:2] == ["profile", "remove"] and argv[-1] == "--json":
    name = argv[2]
    removed = state["profiles"].pop(name, None) is not None
    save()
    print(json.dumps({"ok": True, "removed": removed, "profile": name}))
else:
    save()
    print(json.dumps({
        "ok": False,
        "error": {
            "code": "unexpected_command",
            "message": "commande inattendue: " + " ".join(argv),
        },
    }))
    raise SystemExit(2)
"""


class Context:
    def __init__(self, command):
        self.config = {"command": command}
        self.events = []
        self.logger = None

    def report_diagnostic(self, message, status, **kwargs):
        self.events.append((message, status, kwargs))


def test_profile_lifecycle_through_real_subprocess_json_contract():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        script = root / "fake-kobo-adept.py"
        state = root / "state.json"
        script.write_text(FAKE_CLI, encoding="utf-8")

        runtime = KoboAdeptRuntime(
            Context([sys.executable, str(script), "--state", str(state)])
        )

        contract = runtime.contract()
        assert contract["contract_version"] == 8
        capabilities = runtime.capabilities()
        assert capabilities["capabilities"]["adept"]["profile_provisioning"]["available"] is True
        assert runtime.profiles()["profiles"] == []

        created = runtime.provision_profile("alexandre", "a@example.test")
        profile = created["profile"]
        assert profile["name"] == "alexandre"
        assert profile["complete"] is True
        assert profile["fetch_capable"] is True
        assert profile["provisioning"]["acsm_fetch_ready"] is True

        mount = root / "reader"
        mount.mkdir()
        associated = runtime.associate_profile("alexandre", mount)
        assert len(associated["profile"]["devices"]) == 1

        removed = runtime.remove_profile("alexandre")
        assert removed["ok"] is True
        assert removed["removed"] is True
        assert runtime.profiles()["profiles"] == []

        recorded = json.loads(state.read_text(encoding="utf-8"))["calls"]
        assert ["profile", "add", "alexandre", "--username", "a@example.test", "--json"] in recorded
        assert ["profile", "associate", "alexandre", "--mount", str(mount), "--json"] in recorded
        assert ["profile", "remove", "alexandre", "--json"] in recorded
        assert all("--force" not in call for call in recorded)
