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

Les mesures couvrent des commits et runners de charges différentes ;
elles ne constituent pas un benchmark contrôlé sur machine réservée.
La comparaison est indicative et ne permet pas de déduire une garantie
universelle de gain en pourcentage.

## Décision conditionnelle

Une seconde exécution PR CI complète doit confirmer zéro échec et une
durée sensiblement inférieure au meilleur essai à deux workers (193,85 s).
Objectif de validation : moins de 175 s, soit >10 % plus vite que 193,85 s.
Les gates sont à vérifier sur le dernier HEAD après ce rapport.

`main` reste à deux workers tant que cette expérience n'est pas livrée
et vérifiée. Toute régression de test ou perte de couverture est bloquante.
