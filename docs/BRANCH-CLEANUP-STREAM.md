# Nettoyage progressif des branches distantes (sans plan JSON)

Le script `scripts/nettoyer_branches_git_stream.py` parcourt les branches de
`pzia/cifereca`, les classe et supprime chaque candidate immédiatement
(`--apply`), avec `git push --force-with-lease` fondé sur son HEAD observé.
**Aucun plan figé n'est utilisé**, donc un changement de `main` n'entraîne
pas un rejet global : le critère est recalculé sur le HEAD actualisé.

## Depuis un clone complet local

Prérequis : `git` (avec `git merge-tree --write-tree`), `gh auth login`,
Python 3.10+ ; `origin` doit être `github.com/pzia/cifereca`.

```bash
git pull --ff-only origin main
test -e /tmp/cifereca-exclusions.txt || touch /tmp/cifereca-exclusions.txt

# Prévisualisation (aucune suppression)
python3 scripts/nettoyer_branches_git_stream.py

# Suppression immédiate des R0 et des branches équivalentes à main
python3 scripts/nettoyer_branches_git_stream.py --apply

# OPTION DESTRUCTIVE : supprime aussi les branches divergentes/non fusionnées
# hors protections. Peut faire perdre des commits : vérifier les exclusions.
python3 scripts/nettoyer_branches_git_stream.py --apply --force-divergent
```

Protections automatiques : `main`, branches de release/production/etc.,
branche checkoutée, branches des PR ouvertes (relues toutes les 20
candidates), branches **mentionnées explicitement dans les corps d'issues P0
ou P1 ouvertes**. Pour les handoffs et travaux sans PR non référencés dans ces
issues, inscrire les **noms** ou motifs `fnmatch` dans
`/tmp/cifereca-exclusions.txt`, un par ligne. Une ligne `!nom-exact`
force la classification `FORCÉ`, sauf pour les protections automatiques.

La détection des issues actives et PR requiert `gh`, faute de quoi le script
s'arrête ; elle reste une observation ponctuelle et non un verrou atomique
sur GitHub. Le script revalide le HEAD avant la suppression et ignore une
branche modifiée depuis l'inventaire initial.

Classes : `R0` (HEAD ancêtre de main), `ÉQUIVALENT` (fusion virtuelle
arbre identique, **pas une preuve de PR fusionnée**), `FORCÉ` (suppression
malgré divergence). Les catégories `FORCÉ` peuvent détruire des commits qui
n'existent pas sur main.

Journal CSV progressif : `/tmp/cifereca-purge-stream.csv`, imprimé en fin
d'exécution. Les suppressions GitHub ne sont **pas** réalisées par ChatGPT :
l'utilisateur lance explicitement `--apply` depuis son poste.

Test hors réseau :
```bash
python3 -m unittest scripts.test_nettoyer_branches_git_stream
```
