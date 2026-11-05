# Expérience CI P2 #4573 — parallélisme Core (05/11/2026)

## Dispositif

`pytest -q --durations=10 tests`, répartition par fichier `--dist=loadfile`.
Les tests ciblés restent séquentiels. Aucun skip de test n'est ajouté.

| Politique | Workflow / SHA | Tests pytest | Durée pytest |
| --- | --- | --- | ---: |
| séquentiel | Test Core, `f2dd869b`, run #41981591490 | 16 444 passés, 23 ignorés | 321,62 s |
| séquentiel | Test Core, `9180ab94`, run #41978126953 | 16 412 passés, 23 ignorés | 417,85 s |
| 2 workers | PR CI, `95a05e6e`, run #42025166015 | 16 545 passés, 27 ignorés | 193,85 s |
| 2 workers | Test Core main, `989bb5f4`, run #42026356526 | 16 556 passés, 23 ignorés | 255,03 s |
| 3 workers | PR CI, `c83c4b82`, run #42027408733 | 16 556 passés, 23 ignorés | **146,32 s** |
| 3 workers | PR CI, `566b0415`, run #42028637913 | 16 570 passés, 23 ignorés | **146,52 s** |

Les mesures couvrent des commits et runners de charges différentes ;
elles ne constituent pas un benchmark contrôlé sur machine réservée.
La comparaison est indicative et ne permet pas de déduire une garantie
universelle de gain en pourcentage.

## Décision après répétition

Deux exécutions indépendantes du PR CI ont confirmé le succès exhaustif
des tests Core avec trois workers et une durée de **146,32–146,52 s**.
Le gain indicatif est d'environ 24 % face au meilleur essai PR CI
à deux workers (193,85 s) ; les runners et les commits diffèrent.
Les autres contrôles du PR CI sont passés sur ces deux HEAD.

Le réglage trois workers est candidat à la fusion **pour les seuls tests
Core exhaustifs du PR CI** ; les scopes ciblés restent séquentiels et
`Test Core` sur `main` demeure à deux workers. Le commit de la présente
documentation doit repasser les gates sur son HEAD avant fusion.

Toute régression de test ou perte de couverture reste bloquante.
