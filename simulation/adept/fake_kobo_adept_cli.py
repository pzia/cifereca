#!/usr/bin/env python3
"""Fausse CLI Kobo-Adept v8 pour simuler des profils sans secrets ni DRM.

Le script ne contacte aucun service, ne lit aucun profil de l'utilisateur et
n'écrit que dans le journal temporaire explicitement fourni par le test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def reply(value: object, *, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return code


def command(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--log", type=Path, default=None)
    args, action = parser.parse_known_args(argv)
    document = json.loads(args.fixture.read_text(encoding="utf-8"))
    if document.get("synthetic") is not True or not isinstance(document.get("profiles"), list):
        return reply({"ok": False, "error": {"code": "unsafe_fixture", "message": "JDD non synthétique"}}, code=2)

    # Never log usernames, mount paths, profile names, or tokens.
    if args.log is not None:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"operation": action[:2], "force_requested": "--force" in action}) + "\n")

    if action == ["contract", "--json"]:
        return reply({"ok": True, "name": "kobo-adept", "contract_version": 8,
                      "capabilities": ["strict_json_stdout"]})
    if action == ["capabilities", "--json"]:
        return reply({"ok": True, "capabilities": document["capabilities"]})
    if action == ["profile", "list", "--json"]:
        return reply({"ok": True, "profiles": document["profiles"]})

    if len(action) >= 4 and action[:2] == ["profile", "show"] and action[-1] == "--json":
        name = action[2]
        profile = next((row for row in document["profiles"] if row.get("name") == name), None)
        if profile is None:
            if name in document.get("absent", []):
                return reply({"ok": False, "error": {
                    "code": "profile_missing", "message": "Profil absent",
                }}, code=1)
            if name.startswith("-") or not name.strip():
                return reply({"ok": False, "error": {"code": "invalid_profile", "message": "Profil invalide"}}, code=2)
            profile = {"name": name, "complete": False, "fetch_capable": False,
                       "acsm_fetch_ready": False, "devices": [],
                       "provisioning": {"state": "created", "acsm_fetch_ready": False}}
        return reply({"ok": True, "profile": profile})

    if len(action) >= 5 and action[:2] == ["profile", "add"] and action[-1] == "--json" and "--username" in action:
        # A profile created by the fake stays incomplete until explicitly
        # provisioned, like a freshly created profile with no ADEPT identity.
        return reply({"ok": True, "profile": {"name": action[2], "complete": False,
                      "fetch_capable": False,
                      "provisioning": {"state": "created", "acsm_fetch_ready": False}}})
    if len(action) >= 5 and action[:2] == ["profile", "associate"] and action[-1] == "--json" and "--mount" in action:
        name = action[2]
        profile = next((row for row in document["profiles"] if row.get("name") == name), None)
        if profile is None:
            return reply({"ok": False, "error": {"code": "profile_missing", "message": "Profil absent"}}, code=1)
        return reply({"ok": True, "profile": profile, "simulated": True})
    if len(action) == 4 and action[:2] == ["profile", "remove"] and action[-1] == "--json":
        return reply({"ok": True, "removed": False, "simulated": True})
    return reply({"ok": False, "error": {
        "code": "unsupported_fake_command", "message": "Commande non supportée par le faux runtime",
    }}, code=2)


if __name__ == "__main__":
    raise SystemExit(command())
