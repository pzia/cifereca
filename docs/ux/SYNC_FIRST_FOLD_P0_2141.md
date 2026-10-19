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

La comparaison `--before` retourne désormais un objet `comparison.samples`
à schéma fixe. Pour chaque largeur/police, elle **refuse de calculer des
deltas** si une mesure manque, si les dimensions de fenêtre diffèrent
de plus de 24 px en largeur ou 48 px en hauteur, ou si la population de
sources/actions primaires change. Elle indique alors un motif fixe
(`missing_sample`, `different_window_geometry` ou
`different_source_population`). Sur observations comparables, les
deltas sont *après moins avant* : hausse des intersections sources/actions
et baisse de `scroll_viewports_delta` peuvent aider à guider la revue
visuelle, mais ne sont ni une note d'utilisabilité ni une preuve de
cliquabilité. Ne pas comparer des fixtures ni des états métier différents.

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

## Distinguer intersection et action réellement affichée (mesures V2)

Un bouton dont **un seul pixel** touche le viewport est compté dans
`visible_primary_source_actions`, ce qui **surestime** potentiellement
l'accès aux actions. Le relevé GTK contient désormais aussi :

- `fully_visible_source_status_rows` : sources dont toute la ligne de
  statut tient dans le viewport (pas seulement son début) ;
- `fully_visible_primary_source_actions` : premier bouton de la ligne
  complètement affiché horizontalement **et** verticalement ;
- `fully_visible_enabled_primary_source_actions` : sous-ensemble de ces
  premiers boutons qui sont actifs (`get_sensitive()`) ; *activation
  possible* ne veut pas dire réellement cliquable ou bien nommée ;
- `fully_visible_action_buttons` : tous les boutons complètement affichés ;
- `horizontally_clipped_primary_source_actions` : boutons principaux
  partiellement hors viewport horizontal (le contenu interne du libellé
  n'est **pas** mesuré) ;
- `global_action_fully_visible` : la commande globale tient-elle
  entièrement dans l'écran ?

L'analyseur fournit des pourcentages de **visibilité complète** séparés
des anciennes intersections, et des deltas si **les deux** rapports ont
ces métriques. Il conserve la lecture des anciens rapports mais **n'invente
jamais** des zéros ou une amélioration quand un seul rapport dispose
des champs V2 (`full_visibility_comparable=false`).

Pour éviter de comparer des configurations incomparables, les dimensions du
**viewport** (tolérances : 32×48 px) et le nombre de lignes de détail,
boutons et lignes de résumé sont maintenant vérifiés en plus de la
fenêtre et de la population de sources. Garder une fixture constante entre
avant/après. En présence d'une refonte qui réduit volontairement les lignes,
ces mesures deviennent non comparables : cela demande une **revue
visuelle**, pas une note artificielle.

## Grille d'interprétation

| Mesure | Interprétation, à confronter au PNG |
| --- | --- |
| `window_width`, `target_width_reached` | La largeur réelle est-elle dans la plage 1240–1300 px lors du test 1280 ? |
| `sources_visible_percent` | Part des cartes sources au moins partiellement dans le premier viewport ; **un seul pixel compte** |
| `primary_actions_visible_percent` | Part des premiers boutons des cartes visibles au moins partiellement, sans jugement de cliquabilité ni d'intelligibilité |
| `primary_actions_fully_visible_percent` | Part des premiers boutons **entièrement** contenus dans le premier viewport ; plus exigeant qu'une intersection partielle |
| `enabled_primary_actions_fully_visible_percent` | Part des premiers boutons entièrement affichés et sensibles, sans préjuger du consentement ou de l'activation XTEST |
| `horizontally_clipped_primary_source_actions` | Premier bouton de carte intersectant le viewport mais hors de ses limites horizontales ; ne détecte pas un libellé tronqué *dans* le bouton |
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
