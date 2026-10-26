#!/usr/bin/env python3
"""Purge progressive pzia/cifereca, sans plan statique, avec GH et lease Git."""
import argparse
import csv
import fnmatch
import json
import re
import subprocess
from pathlib import Path

REPO = "pzia/cifereca"
KEEP = ("main", "master", "dev", "develop", "development", "staging",
        "production", "stable", "release", "release/*", "releases/*",
        "hotfix/*", "support/*")


def cmd(*args, check=True):
    p = subprocess.run(args, text=True, capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(args[:3])} : {p.stderr.strip()}")
    return p


def git(*args, check=True):
    return cmd("git", *args, check=check)


def gh(*args):
    return json.loads(cmd("gh", *args).stdout)


def names_from_file(path):
    if not path.exists():
        raise RuntimeError(f"Fichier d'exclusions absent : {path}")
    return [line for raw in path.read_text().splitlines()
            if (line := raw.strip()) and not line.startswith("#")]


def head_map(*patterns):
    result = {}
    for line in git("ls-remote", "--heads", "origin", *patterns).stdout.splitlines():
        sha, ref = line.split("\t", 1)
        result[ref.removeprefix("refs/heads/")] = sha
    return result


def open_prs():
    data = gh("pr", "list", "-R", REPO, "--state", "open", "--limit", "1000",
              "--json", "headRefName")
    return {pr["headRefName"] for pr in data}


def active_issue_refs(branches):
    # Une branche citée dans une issue P0/P1 ouverte reste protégée, même sans PR.
    descriptions = []
    for priority in ("P0", "P1"):
        rows = gh("issue", "list", "-R", REPO, "--state", "open",
                  "--label", priority, "--limit", "1000", "--json", "body")
        descriptions.extend(item.get("body") or "" for item in rows)
    return {name for name in branches if any(name in body for body in descriptions)}


def classify(head, main_sha, main_tree, forced):
    if forced:
        return "FORCÉ"
    if git("merge-base", "--is-ancestor", head, main_sha, check=False).returncode == 0:
        return "R0"
    p = git("merge-tree", "--write-tree", main_sha, head, check=False)
    if p.returncode == 0 and p.stdout.splitlines()[:1] == [main_tree]:
        return "ÉQUIVALENT"  # fusion virtuelle sans modification de l'arbre main
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="supprimer au fil de l'eau")
    parser.add_argument("--force-divergent", action="store_true",
                        help="inclure TOUTES les branches non protégées, même non fusionnées")
    parser.add_argument("--exclude", default="/tmp/cifereca-exclusions.txt",
                        help="lignes = conserver ; !nom exact = forcer")
    parser.add_argument("--log", default="/tmp/cifereca-purge-stream.csv")
    args = parser.parse_args()
    remote = git("remote", "get-url", "origin").stdout.strip()
    if not re.search(r"(?:github\.com[:/])pzia/cifereca(?:\.git)?/?$", remote, re.I):
        raise RuntimeError("origin ne pointe pas vers pzia/cifereca")
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise RuntimeError("Clone shallow non pris en charge")
    rules = names_from_file(Path(args.exclude))
    exclusions = [item for item in rules if not item.startswith("!")]
    forced = {item[1:] for item in rules if item.startswith("!")}
    for branch in forced:
        if git("check-ref-format", "--branch", branch, check=False).returncode != 0:
            raise RuntimeError(f"Forçage invalide : {branch!r}")
    local = git("branch", "--show-current").stdout.strip()
    print("Récupération des branches distantes…", flush=True)
    git("fetch", "--no-tags", "origin", "+refs/heads/*:refs/remotes/origin/*")
    branches = head_map()
    if "main" not in branches:
        raise RuntimeError("main introuvable")
    protected_issue_refs = active_issue_refs(branches)
    protected_prs = open_prs()
    main_sha = branches["main"]
    main_tree = git("rev-parse", f"{main_sha}^{{tree}}").stdout.strip()
    print(f"{len(branches)} branches ; {len(protected_prs)} PR ouvertes ; "
          f"{len(protected_issue_refs)} branches référencées par P0/P1", flush=True)
    counts = {"SUPPRIMÉ": 0, "SIMULATION": 0, "IGNORÉ": 0, "ÉCHEC": 0}
    log_path = Path(args.log)
    with log_path.open("w", newline="", encoding="utf-8") as logfile:
        writer = csv.writer(logfile)
        writer.writerow(["branche", "catégorie", "résultat", "head_sha"])
        candidates_seen = 0
        for branch, observed_sha in sorted(branches.items()):
            if (branch in KEEP or branch == local or branch in protected_prs
                    or branch in protected_issue_refs or
                    any(fnmatch.fnmatchcase(branch, pattern) for pattern in KEEP)):
                continue
            explicit = branch in forced
            if not explicit and any(fnmatch.fnmatchcase(branch, pat) for pat in exclusions):
                continue
            # Vérifier l'état courant sur GitHub avant chaque suppression.
            latest = head_map("refs/heads/main", f"refs/heads/{branch}")
            if branch not in latest or latest[branch] != observed_sha:
                print(f"IGNORÉ (HEAD changé) {branch}", flush=True)
                counts["IGNORÉ"] += 1
                continue
            if latest.get("main") != main_sha:
                git("fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
                main_sha = latest["main"]
                main_tree = git("rev-parse", f"{main_sha}^{{tree}}").stdout.strip()
                print("main a avancé : critères actualisés", flush=True)
            category = classify(observed_sha, main_sha, main_tree, explicit)
            if category is None and args.force_divergent:
                category = "FORCÉ"
            if category is None:
                continue
            candidates_seen += 1
            # Revalider les PR à intervalles réguliers. En cas d'échec GH : arrêt.
            if candidates_seen % 20 == 1:
                protected_prs = open_prs()
            if branch in protected_prs:
                print(f"IGNORÉ (PR ouverte) {branch}", flush=True)
                counts["IGNORÉ"] += 1
                continue
            if not args.apply:
                result = "SIMULATION"
            else:
                r = git("push", f"--force-with-lease=refs/heads/{branch}:{observed_sha}",
                        "origin", f":refs/heads/{branch}", check=False)
                result = "SUPPRIMÉ" if r.returncode == 0 else "ÉCHEC"
            counts[result] += 1
            writer.writerow([branch, category, result, observed_sha])
            logfile.flush()
            print(f"{result:11s} {category:10s} {branch}", flush=True)
    print(f"Bilan : {counts} | journal : {log_path}")
    return 1 if counts["ÉCHEC"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"ERREUR : {exc}")
