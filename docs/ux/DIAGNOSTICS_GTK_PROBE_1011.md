# Recette UX Diagnostics P0 — preuves GTK4 exécutées (#1011)

**Statut au 11 octobre 2026 : deux scénarios E2E PASS sur runner GitHub
standard Ubuntu 24.04**, à 1366 × 900 ; la recette exhaustive D01–D16
reste ouverte. Les scénarios de navigation sont proposés dans la
[PR UX #1146](https://github.com/pzia/cifereca/pull/1146) et ne sont **pas
encore fusionnés** sur `main`.

- Run [E2E Smoke #38132474643](https://github.com/pzia/cifereca/actions/runs/38132474643) :
  **SUCCESS global**, incluant Core, Recherche, Diagnostics et Jeunesse.
- [Artefact GitHub #11699662411](https://github.com/pzia/cifereca/actions/runs/38132474643/artifacts/11699662411) :
  `e2e-core-debug`, rapports JSON, traces, journaux et PNG.
- Commit réellement recetté (lu dans `screen.json`) :
  `8e7c4fa9ca2de4a11a72826d5070bb3f6328de50`.

## Exécuter sur un runner GTK préparé

Depuis la racine du dépôt, avec `xvfb-run`, GTK4/libadwaita,
PyGObject, Pillow et le Python venv `--system-site-packages` :

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-1366
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-navigation-1366
```

`CIFERECA_E2E_PYTHON` peut sélectionner le Python de la recette ;
`E2E Smoke` le prépare automatiquement. La seconde commande requiert
la PR #1146 ou une révision ultérieure intégrant ce scénario.
Voir [environnements/E2E-UX.md](../../environnements/E2E-UX.md)
pour les recettes d'hôte et le bootstrap rootless Debian.

## Scénario 1 — widget Diagnostics réel, contexte simulé

`diagnostics-1366` monte la **vraie contribution GTK du plugin**
dans une fenêtre dédiée, pas dans le shell Cifereca complet.
Le rapport `diagnostics-1366/screen.json` contient `status=PASS`
et six assertions : chargement sans vérification active ni IA implicite,
rapport support repliable/agrandi, JSON indenté expurgé, activation du
bouton de copie au clavier XTEST, presse-papiers égal au JSON affiché
et expurgé, pagination des journaux **50 → 15**.

Les quatre captures sont :

- `00-diagnostics-loaded.png` ;
- `01-report-expanded.png` ;
- `02-structured-json.png` ;
- `03-journals-second-page.png`.

## Scénario 2 — vraie fenêtre Cifereca, navigation physique

`diagnostics-navigation-1366` construit une **vraie `MainWindow`**
et y injecte la vraie contribution `ui.page.diagnostics`. Le gestionnaire
de plugins et les brokers sont des **doubles locaux déterministes** :
ce n'est **pas** la preuve d'une installation de ZIP ou d'un profil réel.

Le rapport `diagnostics-navigation-1366/screen.json` confirme
`status=PASS` avec six assertions et **quatre gestes souris XTEST** :
sélection Bibliothèque, dépliage « Configuration / Technique »,
sélection Diagnostics, retour Bibliothèque. Le titre, la route et la
ligne de navigation sélectionnée ont été vérifiés. Le broker a reçu
**une requête de lecture des événements** et **zéro contrôle actif**.

Quatre captures `00-real-cifereca-shell.png`,
`01-library-selected.png`, `02-real-diagnostics-route.png` et
`03-return-library.png` ont des empreintes SHA-256 concordantes
avec le rapport JSON ; écran **1366 × 900**.

## Limitations et suites

Ces PASS ne couvrent pas, à eux seuls, les **D01–D16** de
[la matrice de recette](DIAGNOSTICS_P0_RECETTE_1011.md) : données
extrêmes (10 000 événements), erreur broker, réseau négatif
instrumenté, redimensionnement/1600 px, parcours complet clavier
`Tab`/`Shift+Tab`/`Esc`, export support sur disque, navigation
avec plugin réellement installé et comptes/identités isolés.
Ne pas fermer #1011 sans ces vérifications et sans captures.

Le CI général de la PR #1146 était **rouge pour une cause indépendante
des E2E** : inventaire des manifestes `cifericaopds` et `dev-updater`,
suivi dans [#1148](https://github.com/pzia/cifereca/issues/1148).
Le PASS E2E ne remplace pas ce gate ; aucune fusion ne doit contourner
le CI rouge.

L'ancienne suspension du budget GitHub Actions a été levée par
l'utilisateur après passage du dépôt en public ; le workflow
`E2E Smoke` existant suffit. Aucun nouveau workflow CI n'est requis.
