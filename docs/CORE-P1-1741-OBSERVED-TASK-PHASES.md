# Core P1 #1741 — mesures observées de tâches, lot M1/M2

Ce lot complète **`gui/ui_perf.py::UiPerfMonitor`**, déjà utilisé pour les
durées de rendu, sans installer de profiler, sans nouvelle tâche/connexion
et sans produire une deuxième base de métrologie.

## Mesures réelles

Pour chaque tâche exécutée via `TaskRunner`, trois phases mesurées
**indépendamment** avec `time.monotonic()` sont rattachées à son identifiant
numérique éphémère : `queue_wait` (soumission → entrée worker),
`worker` (corps de tâche, y compris exception) et `gtk_delivery_wait`
(fin du Future → début du callback GLib). Une tâche restée en file et annulée
avant son exécution ne possède **pas** d'échantillon worker. La phase
`gtk_delivery_wait` n'est pas une durée de rendu ni une latence frame GTK.

Les catégories et déclencheurs acceptés sont des codes publics fermés ;
les catégories inconnues se replient sur `other`. Aucune `label`, chemin,
identité, compte, credential, URL, contenu d'exception ni titre n'est conservé
dans les métriques. Le compteur `operation_id` est le numéro de tâche en
mémoire **et non** un identifiant de livre ou de source.

## API et enveloppe

```python
monitor = window.ui_perf
report = monitor.performance_report()  # aucun accès réseau ni provider
monitor.enable_detailed_capture(True)  # action explicite de l'utilisateur
# Réaliser l'opération à diagnostiquer, sans la relancer pour mesurer.
report = monitor.performance_report()
monitor.enable_detailed_capture(False)
```

L'export est un dictionnaire JSON sérialisable
`cifereca.performance.phases/1`, avec `measurement_origin` explicite
(`observed`, `simulated` ou `unknown`), `no_samples`,
`stats` par catégorie/phase (count/p50/p95/max),
`events` (chronologie bornée uniquement sur opt-in),
`events_dropped` (surcharge), `invalid_samples` et `limits`.
`remote_upload: false` n'est pas une action : aucune remontée automatique.

Le collecteur retient au plus 1 000 durées par série (200 par défaut) et
1 000 événements (240 par défaut). Les valeurs non finies, négatives et
dépassant 24 h sont rejetées. La capture détaillée est **désactivée au départ** ;
l'activer efface son ancienne chronologie, pas les agrégats passifs.
Les statistiques historiques `snapshot()` de rendu restent disponibles
(`render`, `render_library`, `render_recent`, les trois phases de
démarrage). Depuis le lot de sécurisation P1 #1741, les mesures GTK historiques
`list:{titre libre}:append|append-fast|replace` sont **agrégées par action**
sous `list.append`, `list.append-fast`, `list.replace` plutôt que par titre
de page. Le titre pouvait contenir des informations privées et provoquer une
cardinalité de séries illimitée. Les noms inconnus sont agrégés sous `other`
et les durées invalides sont ignorées. Les chronologies `record_phase()`,
les étapes de tâche, les durées et les mesures `render_library` ne changent
pas. La comparaison de performance par titre libre n'est donc volontairement
plus disponible dans la sortie Diagnostics ; cela n'infère pas une amélioration
du rendu ou une latence frame GTK.

## Hors de ce lot (suite #1741)

Le temps d'attente de verrou, les vrais octets/s, les hits cache, les appels
fournisseur, la frame affichée, CPU/RSS, les numéros de phases métier Kobo
et la vue Plugins Diagnostics « Performance » ne sont **pas** mesurés ici.
Ne jamais déduire ces valeurs des trois durées mesurées.

Le rôle Plugins Diagnostics devra consommer `performance_report()` au lieu
de reconstruire les séries avec des messages TRACE. Les recettes GTK 1366/1600
et l'installation utilisateur restent nécessaires pour une qualification de
terrain ; la simulation de `TaskRunner` dans les tests est explicitement une
mesure `simulated`.
