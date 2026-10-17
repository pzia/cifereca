# Core P1 #1741 — coût reproductible des producteurs de diagnostics

Depuis le checkout, dans un environnement avec les dépendances Core :

```bash
PYTHONPATH=src python scripts/benchmark_diagnostics.py --blocks 30 --iterations 100
```

La commande écrit un JSON sur stdout. Elle ne lance ni application, ni plugin,
ni fournisseur, ne charge aucune configuration et n'utilise que des données
fixes synthétiques. Elle n'est raccordée à aucun démarrage ou job périodique.

Sept cas partagent le même processus et les mêmes horloges : appel vide de
référence ; `Diagnostics` désactivé, niveau errors, normal puis detailed ;
`UiPerfMonitor` passif puis détaillé. Une action Diagnostics appelle deux fois
le vrai producteur (un événement normal et un détaillé, avec expurgation d'une
URL synthétique). Une action moniteur appelle `record` et `record_phase` avec
des durées métier fictives fixes : on mesure le coût des appels, **pas** ces
durées fictives et **pas** une latence d'interface.

Une passe complète de chauffe est exclue ; l'ordre des cas tourne à chaque
bloc. Les producteurs sont recréés avec leurs buffers vides hors timer à
chaque bloc ; ce n'est pas une mesure de buffers déjà pleins en régime établi.
La sortie donne le nombre d'actions, p50/p95 nearest-rank, écart-type
population, variance en µs² et min/max en microsecondes. Le CPU est celui du
processus par bloc, ramené à une action ;
sa distribution porte sur les blocs, pas sur chaque appel. Le coût des
horloges, de la boucle et de la sonde est inclus ; la référence n'est pas
soustraite. Création des producteurs, transition de capture et export sont
hors chronométrage. Les compteurs réels de rétention sont lus après mesure.

Les paramètres sont bornés : 5–100 blocs, 10–10 000 actions par bloc et au plus
100 000 actions retenues par cas. Le JSON exporte uniquement des agrégats et
compteurs. Les tests vérifient budgets, ordre, chauffe, statistiques,
confidentialité et rétention ; aucun seuil de vitesse ne devient une gate CI.

**Limites :** provenance `synthetic_headless`, travail succès uniquement,
aucune mesure de frame GTK, RSS, I/O de journal fichier, coût de désactivation
du plugin Diagnostics ou cause attribuée au GC. Le niveau errors désigne le
filtre du collecteur, pas un niveau de logging. Les écarts-types et valeurs
extrêmes rendent visible la variabilité de l'hôte. Ce banc ne fournit aucun
verdict de performance terrain et ne décide pas d'un mode OFF global ; les
recettes alternées sur une installation réelle de #1741 restent nécessaires.
