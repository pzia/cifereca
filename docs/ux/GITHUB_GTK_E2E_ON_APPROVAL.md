# E2E GTK4 Diagnostics directement dans GitHub Actions

## Pourquoi

Le conteneur local Debian 13 de cette session n'a pas accès aux miroirs APT.
Pour recetter UX #1011, exécuter directement la recette GTK existante sous
GitHub Actions, sans cache Debian à rapatrier.

Cette variante n'est **pas** un remplacement du bootstrap rootless documenté
dans `environnements/openai-chat-debian/README.md` : elle s'applique uniquement
au **conteneur Debian 13 éphémère d'un runner GitHub**.

## Workflow

Branche temporaire : `ux/gtk-e2e-github-on-approval-20261011`.

Fichier : `.github/workflows/gtk-e2e-on-approval.yml`.

- Conteneur `debian:trixie` sur runner `ubuntu-24.04`.
- Paquets GTK4, GI/PyGObject, libadwaita, Xvfb, PIL, XTEST,
  httpx et packaging installés sur le runner **uniquement**.
- Préflight d'import GTK4, Adw, PIL, Gdk/Xvfb.
- `/usr/bin/python3 e2e/run.py --scenario diagnostics-1366`.
- Échec du job si le probe ne rend pas `PASS` et des captures.
- `e2e-artifacts/` publié même en cas d'échec sous le nom
  `ux-gtk-diagnostics-1366`, rétention de deux jours.
- Aucun secret, identité réelle, compte distant ou service LLM requis.
- Permissions GitHub restreintes à `contents: read`.

## Approbation nécessaire pour chaque lancement

Le workflow `push` filtre **à la fois la branche temporaire** et le
**chemin sentinelle** `.github/gtk-e2e-run-request.txt`.
Les commits contenant le workflow et cette documentation **ne le déclenchent
pas**, puisque ce chemin n'a pas été modifié.

**Ne créer/modifier le fichier sentinelle qu'après accord explicite de
l'utilisateur.** Avec le connecteur GitHub, cela fait un nouveau commit
`push` qui peut déclencher une unique exécution.

Le connecteur actuellement disponible permet d'écrire le fichier mais ne
fournit pas l'API `workflow_dispatch`. Ne pas annoncer qu'un job a démarré
avant de retrouver une preuve (run et artefacts).

## Résultats

La vue GitHub `Actions` expose la trace d'exécution et l'artefact
`ux-gtk-diagnostics-1366` comprenant le rapport `screen.json`, le
`summary.json`, les PNG, journaux et traces. L'artefact n'existe qu'après
une exécution effective.

Si le résultat est `FAIL`, corriger le code ou la recette en examinant
les traces ; ne jamais changer le verdict pour simuler une réussite.

La branche temporaire et le workflow associé doivent être fermés/supprimés
après recette. Éviter toute exécution automatisée permanente.

## Suspension — budget GitHub Actions (11 octobre 2026)

**Blocage confirmé par le responsable du dépôt : budget GitHub Actions.**
Les exécutions récentes ont été refusées **avant la première étape** (`steps: null`), y compris le CI habituel. Ne pas relancer les workflows pour diagnostiquer ce symptôme : il ne s'agit pas d'une preuve d'échec de GTK4 ou de PyGObject.

**Décision : suspendre toute consommation de minutes GitHub Actions**, y compris les relais de paquets et les runs UX ; ne pas créer de déclenchement `push`, `pull_request`, `workflow_dispatch` ni relancer des jobs sans nouvelle instruction explicite et levée du blocage budgétaire.

**Architecture retenue :** la recette canonique `environnements/openai-chat-debian/` prépare GTK localement sans root ; GitHub n'était envisagé que comme relais réseau ponctuel des paquets Debian/Ubuntu inaccessibles depuis le conteneur, et **pas** comme plateforme d'exécution des tests UX. Éviter le mélange de paquets Debian et Ubuntu.

**Reprise possible après accord :** confirmer le rétablissement du budget ; fournir un canal d'accès aux paquets, exécuter `bootstrap.py --offline --deb-cache …`, `./gtk-python check_import.py`, puis la recette GTK locale et conserver les captures. En attendant : aucun run, aucune nouvelle PR de bootstrap, pas de conclusion « GTK PASS » pour le scénario Diagnostics.

Historique utile : [run 38121476235](https://github.com/pzia/cifereca/actions/runs/38121476235) (préflight GTK/PyGObject réussi), [run 38124311496](https://github.com/pzia/cifereca/actions/runs/38124311496) (job refusé avant démarrage).
