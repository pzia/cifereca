# P0 #8216 — baseline et triage initial

## Point de départ observé

Main `4fd07df843b9d940746bbfe962a1877eec15d431` : 3 329 fichiers Python nommés test* dans tests, tests des plugins, simulation/tests, scripts et e2e (2 497 dans tests, 802 dans 23 suites plugins, 30 ailleurs). Ce n'est pas le nombre de cas pytest collectés. Le seul groupe de blobs de test identiques comporte cinq `test_contract_038.py` (baen, ebooksgratuits, provider-bnf-catalogue, provider-gutenberg, provider-numilog) : leur ROOT propre vérifie chaque manifeste et les imports de chaque package. Conserver les cas; mutualisation du code à étudier avec les propriétaires.

## Réutilisation des preuves #5376

- Collecte: `python -m scripts.ci_test_collection_inventory --suite tests --output /tmp/collection.json`.
- Exécution: `python -m scripts.ci_test_execution_inventory --suite tests --output /tmp/execution.json`. Le nouveau champ wall_duration_seconds chronomètre pytest (collecte + exécution), distinct de la somme des phases et du temps facturé runner. Même SHA propre, environnement et sélection; conserver aussi versions, durée du job, reprises et nombre de runners. Ne pas comparer une exécution séquentielle instrumentée à une CI xdist comme si elles étaient équivalentes.
- Couverture individuelle opt-in: installer pytest-cov compatible avec pytest; utiliser `COVERAGE_FILE=/tmp/cifereca.coverage PYTEST_ADDOPTS='--cov=src/cifereca --cov-branch --cov-context=test --cov-report=' python -m scripts.ci_test_execution_inventory --suite tests --output /tmp/covered-execution.json`, puis `COVERAGE_FILE=/tmp/cifereca.coverage python -m coverage json --show-contexts -o /tmp/coverage.json`. Pour un plugin adapter suite, source et PYTHONPATH comme dans le harnais existant. Mesurer séparément le coût instrumenté et non instrumenté. Les contextes pytest distinguent les nodeids paramétrés et les phases; les imports hors contexte, subprocess et arcs sans attribution individuelle ne prouvent pas de redondance. Aucune couverture mesurée à ce stade.
- Consolidation: `ci_test_execution_aggregate.py` pour les observations au même SHA. Mutations: `ci_security_mutation_campaign --closure --json`, six témoins existants; ne pas assimiler une erreur de collection à un mutant tué.

## Triage statique et prévention

`python -m scripts.ci_test_quality --output /tmp/test-quality.json` sur HEAD propre. Le contrôle PR existant signale les nouveaux candidats des fichiers de test modifiés, relativement au merge-base; le scan complet compare les AST entre fichiers. Il ne lance pas pytest et n'impose aucun quota. Signaux: constantes vraies, comparaison réflexive, unique assertion de forme, retour configuré comparé directement, skip/xfail et absence d'assertion reconnue. Les helpers, effets de bord, mocks réaffectés, imports et fixtures peuvent invalider ces indices. AST identique ne garantit pas des comportements identiques; renames, variantes alpha-renommées, obsolescence effective et redondance comportementale restent à qualifier. Les erreurs de lecture/analyse sont distinguées du résultat de triage.

Aucune suppression dans ce lot. Étapes suivantes: collecter et exécuter les suites au SHA de référence, couvrir individuellement, qualifier les candidats avec contrats et mutations identiques, réaliser les lots par propriétaire, mesurer avant/après et coût CI net. La baseline existante p0-bounded-test-baseline est limitée à six suites et ne constitue pas une preuve globale. Aucun gain net ni PASS revendiqué avant mesure.
