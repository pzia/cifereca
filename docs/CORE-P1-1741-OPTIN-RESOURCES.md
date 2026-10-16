# Core P1 #1741 — fenêtre CPU/RSS volontaire, sans échantillonnage permanent

Cette tranche enrichit **le même** `gui/ui_perf.py::UiPerfMonitor` et son export
local `cifereca.performance.phases/1`. Aucun nouveau collecteur, processus,
thread, dépendance, réseau ou tâche périodique.

## Où sont prises les mesures ?

- Au clic explicite **Démarrer la capture détaillée**, un seul échantillon
  `time.process_time()` (CPU de tous les threads du processus) est relevé.
  Sur Linux avec procfs disponible, `/proc/self/statm` donne également
  **RSS résident courant** en octets (pages résidentes × taille des pages).
- Au clic **Arrêter la capture**, un second échantillon est prélevé.
  La durée murale utilise l'horloge monotone existante du moniteur.
- L'export **n'effectue aucune mesure supplémentaire** : il lit seulement
  la fenêtre terminée ou le point initial de la capture encore active.
  Ouvrir Paramètres et consulter Diagnostics ne lit pas procfs.

L'entrée `resource_window` du JSON contient :
`state` (not_started/active/stopped), `sample_count`,
`wall_ms`, `process_cpu_ms`, `process_cpu_percent_one_core`,
`rss_start_bytes`, `rss_end_bytes`, `rss_source` et
`peak_rss_measured: false`. Un CPU à 150 % reste légitime lorsque plusieurs
threads travaillent : 100 % = capacité d'un seul cœur sur la période.
L'absence de procfs signifie RSS = **null**, jamais zéro fictif.

Le CPU est **global au processus**, pas une attribution à une opération,
à GTK, à un plugin ou à une source. Le RSS à deux instants n'est **pas un pic** :
il peut manquer les fluctuations intermédiaires. Le compteur `no_samples`
conserve sa signification de **phase métier mesurée**, indépendamment de ces
deux points de ressource. Le schéma existant et les événements restent
rétrocompatibles.

## Confidentialité / coût

La sonde retient seulement des nombres finis bornés, ou `null`; aucun champ
arbitraire du lecteur ni message d'erreur n'est stocké. Une nouvelle capture
efface la fenêtre précédente. L'absence ou l'échec de la lecture système
n'empêche ni la capture détaillée, ni la synchronisation, ni la navigation.
La lecture /proc intervient **deux fois seulement par capture**, jamais
dans les callbacks de rendu ni dans le collecteur GC.

## Pour qualifier les performances

Comparer, **sur la même machine**, des parcours après chauffe en
alternance capture OFF/ON, puis journalisation et plugin Diagnostic
activés/désactivés. Rapporter n, distributions p50/p95, GC, coût des sondes,
CPU de la fenêtre, RSS de début/fin et provenance. Les mesures E2E fictives
ne démontrent pas la performance d'une installation utilisateur. Ce lot ne
mesure pas les frames effectivement peintes, le pic RSS, les attentes réseau
ou la part CPU individuelle de chaque phase ; aucun budget ni verdict PASS
de non-régression n'en est déduit.
