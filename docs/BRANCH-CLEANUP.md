# Nettoyage exceptionnel des branches Git historiques

La référence quotidienne des agents reste **`main` + issues actives + PR ouvertes**
(cf. `AGENTS.md`). L'inventaire global des branches n'est réalisé **que** pour
un audit de maintenance dédié. Script : `scripts/nettoyer_branches_git.py`.

## Critères

- **R0** : HEAD de branche ancêtre de `main` (déjà présent dans l'historique).
- **R1-git** : correspondance `git patch-id` avec un squash identifié par `(#PR)`
  dans `main`, **et** résultat `git merge-tree --write-tree` strictement égal
  à l'arbre de `main`. Vérification conservative ; les faux négatifs restent
  conservés. Git >= 2.38 pour cette preuve.
- **R1-manifeste** : PR fusionnée sur `main`, HEAD de branche **strictement égal**
  au HEAD final attesté, et commit de fusion ancêtre de `main`. Ne fournir que
  des manifestes issus d'une source GitHub fiable (voir format du script).
- **À_CONSERVER / INCONNU / PROTÉGÉ** : jamais supprimées.

Le script emploie **git uniquement** (aucun appel GitHub/`gh` en interne).
`git fetch` récupère explicitement toutes les têtes distantes pour rendre
possible une comparaison même dans un clone initialement limité à `main`.
Il refuse les clones shallow et vérifie l'identité du dépôt cible.

## Procédure contrôlée

Depuis un clone complet de `pzia/cifereca`, fournir des fichiers de protection :

```bash
# Cette commande est EXTERNE au script, requiert gh authentifié.
gh pr list --repo pzia/cifereca --state open --limit 1000 \
  --json headRefName,headRepositoryOwner \
  --jq '.[] | select(.headRepositoryOwner.login == "pzia") | .headRefName' \
  > /tmp/cifereca-pr-ouvertes.txt

# En plus, écrire dans /tmp/cifereca-exclusions.txt les motifs des branches
# de travaux en cours SANS PR (issus des issues et handoffs actifs).

# Simulation, sans suppression.
python scripts/nettoyer_branches_git.py \
  --open-pr-branches /tmp/cifereca-pr-ouvertes.txt \
  --exclude /tmp/cifereca-exclusions.txt \
  --plan /tmp/cifereca-plan.json --report /tmp/cifereca-branches.csv
```

Inspecter **toutes** les candidates dans le CSV/JSON, les branches référencées
par des issues encore actives et les protections des PR ouvertes. **Recréer
immédiatement avant l'application** `/tmp/cifereca-pr-ouvertes.txt` depuis GitHub ;
si la liste change, refaire la simulation et la revue (le script rejettera un
plan dont la liste diffère).

```bash
# L'exemple autorise au maximum 50 suppressions, mais refuse tout le lot si
# le plan en contient davantage : choisir délibérément le plafond revu.
python scripts/nettoyer_branches_git.py \
  --open-pr-branches /tmp/cifereca-pr-ouvertes.txt \
  --exclude /tmp/cifereca-exclusions.txt \
  --plan /tmp/cifereca-plan.json --max-delete 50 --apply
```

`--apply --yes` supprime l'invite interactive, **mais ne contourne ni le plan
identique, ni la liste explicite des PR, ni le plafond**. Pour ne classer que
R0 (et R1 fournis par manifeste), utiliser `--skip-r1-git` à la simulation.
`--list-limit` limite la sortie console ; le CSV/plan reste complet.

## Limite opérationnelle

La liste des PR ouvertes est un **instantané extérieur** : Git seul ne peut
vérifier la création d'une nouvelle PR après sa production. Une PR créée en
parallèle au nettoyage pourrait ne pas être protégée. Ne jamais utiliser ce
script en nettoyage périodique sans un contrôle d'état des PR en temps réel.
Le script vérifie en revanche l'immuabilité de `main` avant et pendant les
suppressions et celle de **chaque HEAD** avec `--force-with-lease`. Il ne
supprime ni issue ni PR et n'effectue jamais de push direct sur `main`.

## Tests hors réseau

```bash
python -m unittest scripts.test_nettoyer_branches_git
```

Les tests utilisent un dépôt bare local temporaire, jamais `pzia/cifereca`.


## Suppression forcée ponctuelle

Le même fichier d'exclusions accepte **`!nom-de-branche-exact`** pour supprimer
une branche même si elle est divergente ou couverte par une exclusion :

```text
# Par défaut, conserver cette branche sans PR :
core/p0-3171-declared-download-guard

# Pour forcer sa suppression, enlever la ligne précédente puis écrire :
# !core/p0-3171-declared-download-guard
```

Une ligne préfixée `!` est **inactive tant qu'elle commence par `#`**.
Elle doit désigner un nom exact, jamais un motif global. Il est également
possible de fournir les noms exacts sans `!` dans un fichier séparé via
`--force-delete-branches FICHIER`.

Le script attribue la catégorie `FORCÉ` : elle **n'atteste aucune fusion**
et peut faire perdre des commits non intégrés. Restent impossibles à forcer :
`main`, les branches des PR ouvertes et la branche actuellement checkoutée.
Un plan JSON format 2 enregistre les noms forcés, HEAD, catégories et preuves
afin de refuser toute modification entre simulation et application ;
`--max-delete`, confirmation et `--force-with-lease` restent obligatoires.

```bash
# SIMULATION (reprendre les autres arguments du scénario précédent)
python scripts/nettoyer_branches_git.py \\
  --open-pr-branches /tmp/cifereca-pr-ouvertes.txt \\
  --exclude /tmp/cifereca-exclusions.txt \\
  --plan /tmp/cifereca-plan.json \\
  --report /tmp/cifereca-branches.csv

# APPLICATION volontaire, après revue du plan et actualisation des PR ouvertes :
python scripts/nettoyer_branches_git.py \\
  --open-pr-branches /tmp/cifereca-pr-ouvertes.txt \\
  --exclude /tmp/cifereca-exclusions.txt \\
  --plan /tmp/cifereca-plan.json \\
  --max-delete 50 --apply
```
