# UX P0 #2141 — lecture locale du premier écran Synchronisation

## Objectif

Comparer **la géométrie réelle** de la MainWindow GTK à 1280–1366 px,
avant/après le correctif CoreUI de densité. Les baselines utilisent 11 sources
et 34 sous-lignes de configuration synthétiques. La lisibilité humaine ne se
déduit **pas** des seuls compteurs : ces valeurs facilitent la revue des PNG
de la vraie application, sans la remplacer.

La PR UX #2168 a ajouté `sync_first_fold_ux`. La PR UX #2172 ajoute deux
mesures `sync_1280_first_fold_ux.normal_font` et `.large_font`, avec la
largeur **effectivement** allouée, différente potentiellement des 1280 px
demandés. Les scénarios `mainwindow-baseline-1366` et
`mainwindow-baseline-1600` exercent le vrai GTK/XTEST avec des sources
simulées, jamais un fournisseur réel.

## Relever sur un environnement GTK privé

Suivre d'abord `environnements/E2E-UX.md`. Avec un Python capable
d'importer GTK4, lancer sur une copie locale du dépôt :

```bash
python3 e2e/run.py --scenario mainwindow-baseline-1366
python3 e2e/sync_first_fold_review.py \
  e2e-artifacts/mainwindow-baseline-1366/screen.json
```

La sortie **sur stdout local uniquement** est un JSON de compteurs :
cartes, sous-lignes, boutons, portions visibles, pourcentages d'actions
principales et de statuts visibles, largeur effective, étendue de scroll.
Les autres champs du rapport GTK (comptes, titres, erreurs, chemins, traces)
sont écartés, pas recopiés. Un second rapport peut être comparé avec
`--before /chemin/privé/screen.json`.

```bash
python3 -m unittest discover -s e2e -p 'test_sync_first_fold_review.py' -v
```

**Confidentialité :** le workflow `E2E Smoke` ne publie **que des verdicts
expurgés**. Ses artefacts publics n'incluent pas `screen.json`, les PNG,
ni les mesures UX ; un run CI vert ne donne donc pas accès à ces
comptages. Ne pas publier, transférer ou copier un `screen.json` privé
pour contourner cette restriction. Une projection publique de seules
mesures agrégées serait une évolution distincte, **rôle CI/CD**,
à vérifier selon #1143.

## Grille d'interprétation

| Mesure | Interprétation, à confronter au PNG |
| --- | --- |
| `window_width`, `target_width_reached` | La largeur réelle est-elle dans la plage 1240–1300 px lors du test 1280 ? |
| `sources_visible_percent` | Part des cartes sources au moins partiellement dans le premier viewport ; **un seul pixel compte** |
| `primary_actions_visible_percent` | Part des premiers boutons des cartes visibles au moins partiellement, sans jugement de cliquabilité ni d'intelligibilité |
| `source_summary_lines`, `detail_rows` | Charge descriptive par rapport aux sources ; n'indique pas la lisibilité de chaque bloc |
| `scroll_extent_viewports` | Volume vertical restant en multiples de hauteur du viewport |
| `global_action_visible` | La commande globale intersecte le viewport ; pas une preuve de son accès au clavier |

Comparer d'abord 1280/1366 et police agrandie, puis les 760/1600 de
non-régression. Rechercher des erreurs/alertes visibles, et non uniquement
des compteurs moyens. Vérifier Tab/Shift+Tab, déplacement vertical, états
désactivés, accord de téléchargement et absence d'action implicite.

**Verdict actuel :** instrumentation et E2E existants seulement. Pas de
preuve d'une résolution utilisateur du P0 #2141 ; il faut un correctif
CoreUI et une recette visuelle sur l'installation représentative.
