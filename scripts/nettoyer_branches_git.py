#!/usr/bin/env python3
"""Nettoyage conservateur des branches distantes de pzia/cifereca, via git.

Niveaux :
  R0 : HEAD distant ancêtre de origin/main (historique entièrement intégré).
  R1 : HEAD divergent, mais soit :
       a) preuve Git d'un squash : patch-id identique à un commit de main
          marqué par un numéro de PR, ET git merge-tree n'ajoute aucun contenu ;
       b) manifeste de PR fusionnées : HEAD identique au dernier HEAD de la PR,
          commit de fusion ancêtre de main et base main attestée.

Le script n'appelle AUCUNE API GitHub ni 'gh' : toutes les vérifications Git,
la lecture des refs et les suppressions passent par l'exécutable 'git'.
Un manifeste R1 est une preuve externe facultative (JSON), PAS un prétexte
pour supprimer des branches dont le HEAD a changé.

IMPORTANT : git ne permet pas d'identifier les PR *actuellement ouvertes*.
Pour appliquer : fournir une liste de leurs branches (instantané externe),
une simulation --plan conservée, et un plafond --max-delete explicite.
Le plan est revalidé avant tout push, et chaque suppression vérifie le HEAD
(force-with-lease). Une PR créée après l'instantané reste un risque résiduel :
revérifier la liste des PR immédiatement avant --apply.
Une ligne !branche dans --exclude (ou une entrée --force-delete-branches)
permet, à la demande explicite de l'opérateur, de supprimer des branches
nommées exactement même si elles figurent parmi les exclusions
ou ne satisfont pas R0/R1. Le plan montre ces suppressions comme FORCÉ,
mais main, les branches de PR ouvertes et la branche actuellement checkoutée
restent interdites. Simulation par défaut ; aucune suppression implicite.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = "pzia/cifereca"
PROTECTED = (
    "main", "master", "dev", "develop", "development", "staging",
    "production", "stable", "release", "release/*", "releases/*",
    "hotfix/*", "support/*",
)
PR_SUFFIX = re.compile(r"\(#([1-9][0-9]*)\)\s*$")
SHA = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class Verdict:
    branch: str
    head: str
    category: str
    evidence: str = ""


def git(*args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if ok and proc.returncode:
        raise RuntimeError(
            f"git {' '.join(args)}\n{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def git_test(*args: str) -> bool:
    proc = git(*args, ok=False)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"git {' '.join(args)} : {proc.stderr.strip()}")
    return proc.returncode == 0


def commit_available(sha: str) -> bool:
    """Un commit absent (exit 128) n'est jamais une raison de suppression."""
    return git("cat-file", "-e", f"{sha}^{{commit}}", ok=False).returncode == 0


def remote_heads(remote: str) -> dict[str, str]:
    data = git("ls-remote", "--heads", remote).stdout
    result: dict[str, str] = {}
    for line in data.splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2 or not fields[1].startswith("refs/heads/"):
            raise RuntimeError("Réponse git ls-remote inattendue")
        result[fields[1].removeprefix("refs/heads/")] = fields[0].lower()
    return result


def patterns_from_file(path: str | None) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"Fichier d'exclusions absent : {p}")
    return [line for raw in p.read_text(encoding="utf-8").splitlines()
            if (line := raw.strip()) and not line.startswith("#")]


def open_pr_names(path: str | None) -> set[str]:
    """Les branches de PR sont des noms EXACTS, jamais des glob patterns."""
    names = patterns_from_file(path)
    for name in names:
        if name.startswith("refs/heads/"):
            raise RuntimeError("Liste PR : utiliser les noms de branches, sans refs/heads/")
        if not git_test("check-ref-format", "--branch", name):
            raise RuntimeError(f"Nom de branche de PR invalide : {name!r}")
    return set(names)


def exclusion_rules(path: str | None) -> tuple[list[str], set[str]]:
    """Lignes ordinaires = protéger ; !branche = supprimer malgré les preuves."""
    retained = []
    forced = []
    for line in patterns_from_file(path):
        if line.startswith("!"):
            forced.append(line[1:])
        else:
            retained.append(line)
    return retained, validate_forced_names(forced)


def validate_forced_names(names: list[str]) -> set[str]:
    for name in names:
        if not name or any(ch in name for ch in "*?["):
            raise RuntimeError(f"Suppression forcée : nom explicite requis, sans glob : {name!r}")
        if not git_test("check-ref-format", "--branch", name):
            raise RuntimeError(f"Suppression forcée : nom de branche invalide : {name!r}")
    return set(names)


def force_delete_names(path: str | None) -> set[str]:
    """Autre fichier de noms exacts, sans préfixe ! ni motifs glob."""
    return validate_forced_names(patterns_from_file(path))


def snapshot(main_sha: str, pr_names: set[str], forced: set[str], candidates: list[Verdict]) -> dict:
    """Données nécessaires pour un consentement explicite sur un ensemble de HEAD."""
    return {
        "format": 2,
        "repository": REPO,
        "main_sha": main_sha,
        "open_pr_branches": sorted(pr_names),
        "force_delete_branches": sorted(forced),
        "candidates": [
            {"branch": v.branch, "head": v.head, "category": v.category,
             "evidence": v.evidence}
            for v in candidates
        ],
    }


def validate_plan(path: str, current: dict) -> None:
    """Une simulation différente, même d'une seule branche, interdit --apply."""
    try:
        approved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Plan de simulation illisible : {exc}") from exc
    if approved != current:
        raise RuntimeError(
            "Le plan ne correspond plus à main, à la liste des PR ouvertes "
            "ou aux branches candidates. Refaire la simulation et la revue."
        )


def load_manifest(path: str | None) -> dict[str, list[dict]]:
    """Refuser toute preuve incomplète. Deux preuves contradictoires => rejet."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("merged_pull_requests") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("Manifeste attendu : {'merged_pull_requests': [...]} ")
    out: dict[str, list[dict]] = {}
    for x in entries:
        if not isinstance(x, dict):
            raise RuntimeError("Chaque entrée du manifeste doit être un objet")
        name = x.get("branch")
        if not isinstance(name, str) or not name or name.startswith("-"):
            raise RuntimeError(f"Nom de branche invalide dans le manifeste : {name!r}")
        if x.get("merged") is not True or x.get("base") != "main" or x.get("head_repo_full_name") != REPO:
            raise RuntimeError(f"Preuve de PR fusionnée dans main insuffisante : {name}")
        if not isinstance(x.get("pr_number"), int) or x["pr_number"] <= 0:
            raise RuntimeError(f"Numéro de PR absent ou invalide : {name}")
        for key in ("pr_head_sha", "merge_commit_sha"):
            if not isinstance(x.get(key), str) or not SHA.fullmatch(x[key]):
                raise RuntimeError(f"SHA non valide : {name} / {key}")
        out.setdefault(name, []).append(x)
    return out


def patch_id(*diff_args: str) -> str | None:
    """Calculer un git patch-id --stable d'un diff sans fichier temporaire.

    Le patch-id seul n'est PAS une preuve suffisante : R1-git exige en plus
    l'équivalence de l'arbre résultant avec main par git merge-tree.
    """
    producer = subprocess.Popen(
        ["git", *diff_args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        consumer = subprocess.Popen(
            ["git", "patch-id", "--stable"], stdin=producer.stdout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except BaseException:
        producer.kill()
        producer.communicate()
        raise
    assert producer.stdout is not None
    producer.stdout.close()
    out, err = consumer.communicate()
    assert producer.stderr is not None
    producer_err = producer.stderr.read()
    producer.stderr.close()
    producer_code = producer.wait()
    if producer_code or consumer.returncode:
        raise RuntimeError(f"Erreur git patch-id : {(producer_err + err).decode(errors='replace')}")
    if not out.strip():
        return None
    field = out.decode("ascii", errors="strict").split(None, 1)[0]
    return field if SHA.fullmatch(field) else None


def squash_index(main_ref: str, max_commits: int) -> dict[str, list[tuple[int, str]]]:
    """Index des patch-id de commits de squash de main avec suffixe (#PR)."""
    raw = git(
        "log", "--first-parent", "--no-merges", f"-n{max_commits}",
        "--format=%H%x09%s", main_ref,
    ).stdout
    idx: dict[str, list[tuple[int, str]]] = {}
    count = 0
    for line in raw.splitlines():
        try:
            commit, subject = line.split("\t", 1)
        except ValueError:
            continue
        m = PR_SUFFIX.search(subject)
        if not m:
            continue
        count += 1
        pid = patch_id("show", "--format=", "--no-ext-diff", "--no-textconv", "--no-renames", commit)
        if pid:
            idx.setdefault(pid, []).append((int(m.group(1)), commit))
    print(f"Preuves squash Git : {count} commits avec suffixe PR analysés ({len(idx)} patch-id uniques)")
    return idx


def r1_manifest_proof(branch: str, head: str, proofs: dict[str, list[dict]], main_sha: str) -> str | None:
    matches = proofs.get(branch, [])
    if not matches:
        return None
    # Si la branche a été réutilisée pour une autre PR, la supprimer serait imprudent.
    if len(matches) != 1:
        return None
    p = matches[0]
    if p["pr_head_sha"].lower() != head.lower():
        return None
    merge_sha = p["merge_commit_sha"].lower()
    if not commit_available(merge_sha):
        return None
    if not git_test("merge-base", "--is-ancestor", merge_sha, main_sha):
        return None
    return f"PR #{p['pr_number']} fusionnée dans main (manifeste, HEAD inchangé)"


def r1_git_proof(
    branch: str, head: str, main_sha: str, main_tree: str,
    commits_by_patch: dict[str, list[tuple[int, str]]],
) -> str | None:
    # Si HEAD est déjà intégré, c'est R0 et cette fonction ne sert pas.
    fork = git("merge-base", main_sha, head, ok=False)
    if fork.returncode:
        return None
    common = fork.stdout.strip()
    if not SHA.fullmatch(common):
        return None
    pid = patch_id("diff", "--no-ext-diff", "--no-textconv", "--no-renames", common, head)
    if not pid or pid not in commits_by_patch:
        return None
    matches = commits_by_patch[pid]
    if len(matches) != 1:
        return None  # ambiguïté volontairement non supprimée
    pr_number, squash_sha = matches[0]
    # Confirmation que le squash lui-même fait partie de l'histoire de main.
    if not git_test("merge-base", "--is-ancestor", squash_sha, main_sha):
        return None
    # Vérification plus stricte que le patch-id : fusion virtuelle de la branche
    # dans main. Elle doit produire EXACTEMENT le même arbre que main.
    # Git >= 2.38 requis. Une fusion conflictuelle ou indéfinie => exclusion.
    virtual = git("merge-tree", "--write-tree", main_sha, head, ok=False)
    if virtual.returncode:
        return None
    candidate_tree = virtual.stdout.strip().splitlines()[0] if virtual.stdout.strip() else ""
    if candidate_tree != main_tree:
        return None
    return f"PR #{pr_number} squash Git {squash_sha[:12]}, arbre identique"


def export_csv(path: str, verdicts: list[Verdict]) -> None:
    # Les branches ne doivent pas commencer par '=' : protéger malgré tout
    # le CSV contre les formules dans un lecteur de tableur.
    def safe(s: str) -> str:
        return "'" + s if s.startswith(("=", "+", "-", "@", "\t", "\r")) else s
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["branch", "head_sha", "niveau", "preuve"])
        for row in verdicts:
            writer.writerow([safe(row.branch), row.head, row.category, safe(row.evidence)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remote", default="origin", help="Remote Git (défaut : origin)")
    ap.add_argument("--base", default="main", choices=["main"], help="Branche de base (main uniquement)")
    ap.add_argument("--exclude", help="Règles : branche ou glob = conserver ; !branche exacte = suppression forcée")
    ap.add_argument("--force-delete-branches", metavar="FILE",
                    help="Noms EXACTS des branches à supprimer même si exclues/divergentes ; jamais main/PR ouvertes")
    ap.add_argument("--open-pr-branches", help="Fichier de noms EXACTS des branches de PR ouvertes (requis pour --apply)")
    ap.add_argument("--r1-manifest", help="JSON de PR fusionnées pour attester R1 sans API")
    ap.add_argument("--skip-r1-git", action="store_true", help="R1 seulement via manifeste (plus rapide)")
    ap.add_argument("--max-squash-commits", type=int, default=2500,
                    help="Nombre maximal de commits récents de main examinés pour R1 Git")
    ap.add_argument("--report", help="Écrire le classement complet dans un CSV")
    ap.add_argument("--plan", help="JSON de simulation : écrire en dry-run, relire et contrôler en --apply")
    ap.add_argument("--list-limit", type=int, default=25,
                    help="Nombre de candidates affichées (défaut 25 ; 0 pour aucune)")
    ap.add_argument("--apply", action="store_true", help="Autoriser les suppressions Git")
    ap.add_argument("--yes", action="store_true", help="Confirmer sans invite (automatisation contrôlée)")
    ap.add_argument("--max-delete", type=int, default=0,
                    help="Plafond de suppressions explicite (obligatoire avec --apply)")
    args = ap.parse_args()
    if args.max_delete < 0 or args.max_squash_commits < 0 or args.list_limit < 0:
        ap.error("Les plafonds doivent être positifs ou nuls")
    if args.yes and not args.apply:
        ap.error("--yes n'a de sens qu'avec --apply")
    if args.apply and (not args.open_pr_branches or not args.plan or args.max_delete <= 0):
        ap.error("--apply exige --open-pr-branches, --plan et --max-delete > 0")
    git("rev-parse", "--is-inside-work-tree")
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        raise RuntimeError("Clone shallow : utiliser un clone complet avant l'audit R0/R1")
    url = git("remote", "get-url", args.remote).stdout.strip()
    if not re.search(r"(?:github\.com[:/])pzia/cifereca(?:\.git)?/?$", url, re.IGNORECASE):
        ap.error(f"Remote inattendu : {url!r} ; attendu github.com/{REPO}")

    proofs = load_manifest(args.r1_manifest)
    excluded_patterns, forced_from_rules = exclusion_rules(args.exclude)
    protected = [*PROTECTED, *excluded_patterns]
    protected_prs = open_pr_names(args.open_pr_branches)
    forced = forced_from_rules | force_delete_names(args.force_delete_branches)
    local = git("branch", "--show-current").stdout.strip()
    forbidden = {args.base, *protected_prs}
    if local:
        forbidden.add(local)
        protected.append(local)  # Ne pas détruire la branche actuellement checkoutée.
    overlap = forced & forbidden
    if overlap:
        raise RuntimeError("Suppression forcée interdite pour main, PR ouverte ou "
                           f"branche checkoutée : {', '.join(sorted(overlap))}")

    print("Récupération de l'historique Git et inventaire des refs distantes…")
    # Un clone --single-branch ne récupère pas les objets des autres HEAD avec
    # un fetch ordinaire. La refspec explicite permet un classement R0/R1 réel.
    git("fetch", "--no-tags", args.remote,
        f"+refs/heads/*:refs/remotes/{args.remote}/*")
    heads = remote_heads(args.remote)
    if args.base not in heads:
        raise RuntimeError(f"Branche distante absente : {args.base}")
    missing_forced = forced - heads.keys()
    if missing_forced:
        raise RuntimeError("Branches demandées en suppression forcée absentes du remote : "
                           + ", ".join(sorted(missing_forced)))
    main_sha = heads[args.base]
    ref = f"refs/remotes/{args.remote}/{args.base}"
    if git("rev-parse", "--verify", ref).stdout.strip().lower() != main_sha:
        raise RuntimeError("main distante modifiée pendant le fetch. Relancer.")
    main_tree = git("rev-parse", f"{main_sha}^{{tree}}").stdout.strip()

    # La preuve externe est vérifiée contre les objets Git ; le mode autonome
    # R1-git utilise un index de squash et un contrôle de fusion virtuelle.
    squash = {} if (args.skip_r1_git or args.max_squash_commits == 0) else squash_index(main_sha, args.max_squash_commits)
    verdicts: list[Verdict] = []
    counts: dict[str, int] = {}
    for name, head in sorted(heads.items()):
        if name in forced:
            category, proof = "FORCÉ", "suppression explicite, sans preuve d'intégration R0/R1"
        elif (name == args.base or name in protected_prs
                or any(fnmatch.fnmatchcase(name, pattern) for pattern in protected)):
            category, proof = "PROTÉGÉ", "motif d'exclusion"
        elif not commit_available(head):
            category, proof = "INCONNU", "objet Git introuvable localement"
        elif git_test("merge-base", "--is-ancestor", head, main_sha):
            # Si une branche R0 correspond à une PR ouverte, Git ne le sait pas.
            category, proof = "R0", "HEAD déjà ancêtre de main"
        else:
            evidence = r1_manifest_proof(name, head, proofs, main_sha)
            if evidence:
                category, proof = "R1-manifeste", evidence
            else:
                evidence = r1_git_proof(name, head, main_sha, main_tree, squash) if squash else None
                if evidence:
                    category, proof = "R1-git", evidence
                else:
                    category, proof = "À_CONSERVER", "historique divergent ou preuve R1 insuffisante"
        verdicts.append(Verdict(name, head, category, proof))
        counts[category] = counts.get(category, 0) + 1

    if args.report:
        export_csv(args.report, verdicts)
    candidates = [v for v in verdicts if v.category in {"R0", "R1-manifeste", "R1-git", "FORCÉ"}]
    plan = snapshot(main_sha, protected_prs, forced, candidates)
    if args.plan:
        if args.apply:
            validate_plan(args.plan, plan)
        else:
            Path(args.plan).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"Remote : {args.remote} ({REPO}) | base main : {main_sha[:12]} | total : {len(heads)}")
    for cat in ("R0", "R1-git", "R1-manifeste", "FORCÉ", "À_CONSERVER", "PROTÉGÉ", "INCONNU"):
        print(f"  {cat:15s} {counts.get(cat, 0)}")
    if args.report:
        print(f"Rapport : {args.report}")
    for v in candidates[:args.list_limit]:
        print(f"  {'DELETE' if args.apply else 'SIMULATION'} {v.category:11s} {v.branch} {v.head[:12]} — {v.evidence}")
    if len(candidates) > args.list_limit:
        print(f"  ... {len(candidates) - args.list_limit} autres candidates ; utiliser --report")
    if not args.apply:
        print("Aucune suppression. Vérifier les branches FORCÉ du plan puis fournir --plan, la liste des PR et --max-delete avec --apply.")
        return 0
    if args.max_delete and len(candidates) > args.max_delete:
        raise RuntimeError(f"{len(candidates)} candidates > --max-delete {args.max_delete} : aucune suppression")
    if not candidates:
        print("Aucune candidate à supprimer.")
        return 0
    # Bloquer si main a bougé entre l'inventaire et le début de l'application.
    if remote_heads(args.remote).get(args.base) != main_sha:
        raise RuntimeError("main distante a changé : aucune suppression. Relancer le script.")
    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError("Session non interactive : utiliser --apply --yes après inspection du rapport")
        answer = input(f"Supprimer {len(candidates)} branches distantes, dont {counts.get('FORCÉ', 0)} forcées ? Taper SUPPRIMER : ")
        if answer != "SUPPRIMER":
            print("Annulé, aucune suppression.")
            return 0

    failures = 0
    deleted = 0
    for index, v in enumerate(candidates):
        # Pendant un gros nettoyage, main peut changer : recontrôler par lots.
        # La lecture du plan et le force-with-lease protègent les HEAD connus ;
        # la création de nouvelles PR nécessite une nouvelle liste externe.
        if index and index % 25 == 0:
            if remote_heads(args.remote).get(args.base) != main_sha:
                print("ARRÊT : main a changé pendant le nettoyage", file=sys.stderr)
                return 1
        expected = f"{v.head}\trefs/heads/{v.branch}"
        latest = git("ls-remote", "--heads", args.remote, f"refs/heads/{v.branch}").stdout.splitlines()
        if expected not in latest:
            failures += 1
            print(f"CONSERVÉ (ref absente ou avancée) : {v.branch}", file=sys.stderr)
            continue
        # La protection contre les modifications concurrentes utilise la
        # vérification SHA du remote dans Git lui-même (force-with-lease).
        cmd = git(
            "push", f"--force-with-lease=refs/heads/{v.branch}:{v.head}",
            args.remote, f":refs/heads/{v.branch}", ok=False,
        )
        if cmd.returncode:
            failures += 1
            print(f"ÉCHEC {v.branch} : {cmd.stderr.strip()}", file=sys.stderr)
        else:
            deleted += 1
            print(f"SUPPRIMÉ {v.category} {v.branch}")
    print(f"Bilan : {deleted} supprimées, {failures} refusées/échouées")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        raise SystemExit(2)