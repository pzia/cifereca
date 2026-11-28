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


## Réception intermédiaire (27 novembre 2026)

Les paragraphes précédents décrivent le point de départ, non l'état actuel.

| Périmètre | Observation | Preuve |
|---|---|---|
| 23 plugins, baseline A | 11 716 cas, 11 701 pass, 15 skips; mêmes identités et statuts plain/couverture | [46059532585](https://github.com/pzia/cifereca/actions/runs/46059532585) |
| Coût instrumentation plugins | Somme des durées pytest 162,73 → 291,14 s (+78,91 %); pas le chemin critique CI | même campagne |
| Simulation, scripts, e2e statique | 240 pass, 44,68 → 52,70 s (+17,95 %); ne couvre pas l'E2E GTK réelle | [46060228655](https://github.com/pzia/cifereca/actions/runs/46060228655) |
| Mutations historiques rejouées sur A | 6/6 tuées | même campagne |
| Triage global | 3 330 fichiers, 19 899 définitions, 11 groupes AST (34 définitions), 64 absences d'oracle reconnu, 15 skip/xfail, 6 formes faibles, zéro erreur | [46061143842](https://github.com/pzia/cifereca/actions/runs/46061143842) |
| Contextes SQLite plugins | 7 880 signatures d'arcs run non vides distinctes (somme par suite), 1 452 groupes répétés, 5 275 contextes membres | [46063743351](https://github.com/pzia/cifereca/actions/runs/46063743351) |
| APSW/POSIX ciblés | 7 pass, aucun skip | [46066190682](https://github.com/pzia/cifereca/actions/runs/46066190682) |

Les groupes AST initiaux ont été qualifiés; les variantes de fournisseurs, de racines, de protocoles et de contraintes sont conservées. Les 64 absences d'oracle ont été examinées, dont des assertions déléguées et des tests de non-exception; le scanner distingue désormais 10 candidats à oracle délégué, sans les déclarer automatiquement valides. Une signature d'arcs identique ne garantit jamais une assertion ou une entrée équivalente.

### Lots reçus et effets mesurés

- #8237 triage statique et durée murale; #8282 distinction des oracles délégués.
- #8238 partage inventaire Core: ABBA, 30 cas identiques à chaque passage; durée pytest sérielle moyenne 137,935 → 27,875 s, xdist 138,205 → 28,055 s ([46059532585](https://github.com/pzia/cifereca/actions/runs/46059532585)).
- #8270 partage parsing Numilog avec deepcopy par cas: 39 cas identiques; ABBA JUnit 1,853 → 0,0955 s ([46063477794](https://github.com/pzia/cifereca/actions/runs/46063477794)).
- #8357 cache de compilation AST: 14 cas identiques; ABBA JUnit 0,3305 → 0,083 s ([46065197369](https://github.com/pzia/cifereca/actions/runs/46065197369)).
- #8246, #8299, #8339: 11 cas renforcés (URLs explicites et appel effectif du sink); mutants discriminants [46061988293](https://github.com/pzia/cifereca/actions/runs/46061988293), [46063974404](https://github.com/pzia/cifereca/actions/runs/46063974404), [46064912950](https://github.com/pzia/cifereca/actions/runs/46064912950).
- #8256: un test de présence SourceKey retiré après comparaison des oracles et deux mutants [46062682158](https://github.com/pzia/cifereca/actions/runs/46062682158).
- #8366, #8369: isolation de répertoire temporaire et du chemin de verrou réel, sans retrait d'assertion; anciens échecs reproduits, nouvelles exécutions répétées vertes ([46066190682](https://github.com/pzia/cifereca/actions/runs/46066190682), [46066668864](https://github.com/pzia/cifereca/actions/runs/46066668864)).

Les gains locaux d'environ 113 s ne constituent ni un gain global mesuré ni une économie facturée. La baseline Core monolithique a été annulée; la campagne fractionnée [46065388826](https://github.com/pzia/cifereca/actions/runs/46065388826) a révélé deux défauts d'isolation. La reprise #8372 applique les deux corrections de test à A et B, conserve les patchs et distingue ces observations de la baseline historique pure. La réception globale reste ouverte.

## Gouvernance durable proposée

- À chaque PR de tests, exploiter le signal delta existant et qualifier les nouveaux candidats dans la revue. Ne pas imposer un nombre maximal de tests, un taux de suppression ou un seuil arbitraire de couverture. Une erreur de scan n'est pas un verdict qualité.
- À chaque changement de contrat, retrait d'API ou modification d'une fixture partagée, chercher les garanties existantes avant d'ajouter un cas. Vérifier les valeurs limites, la portée, les invariants négatifs et la sensibilité de l'oracle; les tests de sécurité et d'isolation restent prioritaires.
- À chaque revue périodique de maintenance (cadence proposée: mensuelle ou déclenchée par hausse mesurée de durée/volume), lancer le scan global et classer les candidats par risque et coût. N'instrumenter que les suites utiles en première intention; réserver la campagne intégrale à une justification explicite. Aucun nouveau job coûteux permanent.
- Avant suppression ou fusion, établir la correspondance des garanties, les nodeids affectés, la preuve de non-régression et un mutant pertinent si possible. Pour une mutualisation, vérifier l'isolation des entrées mutables et les exécutions répétées/parallelisées. Conserver un cas si l'équivalence reste incertaine.
- Pour toute mesure: conserver SHA, sélection, versions, statuts par cas, couverture lignes/arcs et contexte, durées murales, somme runner et coût de l'outillage. Comparer à configuration égale; signaler les skips, les échecs et les reprises. Les couvertures de fichiers renommés ou modifiés exigent une correspondance explicite, non une simple différence de numéros de ligne.
- Les artefacts de campagnes sont temporaires (rétention sept jours). Reporter avant expiration les métriques et résultats décisionnels dans le ticket ou ce document, avec références de runs, SHA, limites et méthode reproductible. La conservation des archives brutes au-delà de cette rétention n'est pas acquise.

La clôture #8216 exige encore la réception Core, la consolidation des garanties et coûts nets, et la qualification proportionnée des groupes comportementaux pertinents. Ce registre intermédiaire n'est pas un certificat de clôture.


## Réception intermédiaire du 28 novembre 2026

Les résultats ci-dessous complètent, sans remplacer, la réception du 27 novembre. Les deux révisions historiques restent A `4fd07df843b9d940746bbfe962a1877eec15d431` et B `e8ed298116e57d44645c37cd3fa7ad91e1e9077e`. Les reprises appliquent des correctifs de tests explicitement tracés aux deux bras; elles ne constituent pas une exécution historique pure.

| Preuve | Résultat et périmètre |
|---|---|
| Collecte canonique [46068678396](https://github.com/pzia/cifereca/actions/runs/46068678396) | 29 041 nodeids A, 29 044 B; zéro identifiant ambigu, zéro hors partition, rapprochement exact des cinq shards reçus. |
| Consolidation [46068572203](https://github.com/pzia/cifereca/actions/runs/46068572203), confirmation [46068678157](https://github.com/pzia/cifereca/actions/runs/46068678157) | Shards 0,2,3,5,6 : 18 195 → 18 198 pass, 23 skips par bras dont cinq de collection; identités/statuts identiques plain/couverture dans chaque bras. Une suppression SourceKey et quatre ajouts de tests qualité. |
| Arcs et durées sur ces cinq shards | 164 332 → 164 343 arcs observés, zéro arc A-only sur fichiers source identiques, 11 B-only; aucun chemin non mappé. Somme temps muraux plain 612,61 → 609,17 s; temps externes instrumentés incomplets. Somme durées suite pytest plain 607,760 → 606,425 s, instrumentées 1 169,469 → 1 174,090 s. Pas un gain global ni une mesure de facturation. |
| Recouvrements [46101626072](https://github.com/pzia/cifereca/actions/runs/46101626072) | 16 549 contextes run A/B; signatures distinctes 11 032 → 11 026; groupes répétés 2 161 → 2 159 (7 678 → 7 682 membres); intermodules 153 → 154 (779 → 786 membres). Candidats, pas équivalence d'oracles. |
| #8377 | CI [46068401908](https://github.com/pzia/cifereca/actions/runs/46068401908), fusion `efa31ffd72b5bbbc7eeebf10a8037b30cb183f54`: garde d'absence d'I/O compatible instrumentation sans retrait d'assertion. |
| #8395 | Témoin [46114214215](https://github.com/pzia/cifereca/actions/runs/46114214215): ancien cas échoue sous contention réelle; module corrigé 14 pass deux fois, détenteur indépendant vivant. CI [46114073006](https://github.com/pzia/cifereca/actions/runs/46114073006), fusion `a3e2a1953e913d0a8f61765e9366721a12bcad81`, blob main vérifié. |

### Candidats et limites

- Préserver #5225 (extensions/formats/validité et témoins réels), #6397/#2797 (preuves d'URL distinctes), #1120 (refus PNB et vérification ADEPT-first distincts), #3196 W1/W2 (propriété, non-réintroduction et archives). Un recouvrement d'arcs ne permet pas de supprimer les frontières.
- #8393 ADEPT : trois refus successifs et contrôle d'absence de mutation après chaque appel, au lieu de trois setups de 1/2/3 appels. Campagne [46114405647](https://github.com/pzia/cifereca/actions/runs/46114405647), ABBA 14/12/12/14 cas, durée JUnit moyenne 0,103 → 0,0855 s; trois mutants identiques tués dans les deux bras (code, accès runtime, écriture persistante archive). Gain local de 17 %, faible valeur absolue, temps de démarrage bruité. Candidat non fusionné, avis métier requis; diminution de répétitions sur fixtures fraîches à arbitrer.
- Kobo executor retries : plusieurs libellés de panne ne sont pas injectés dans le harnais; candidat à qualifier, pas un retrait autorisé. Le cas fichier préexistant a un oracle supplémentaire.
- Shard 1 : deux refus de transfert canonique sous coverage, cause non établie; une exécution diagnostique modifiée a réussi mais n'est pas une preuve baseline. Shards 4 et 7 : annulations et reprises, dont contention réelle pour 4. Le harnais sépare désormais bases coverage, logs et chronomètres pour éviter collision des fichiers de combinaison. Nouvelle campagne [46114204645](https://github.com/pzia/cifereca/actions/runs/46114204645) et diagnostic [46114531406](https://github.com/pzia/cifereca/actions/runs/46114531406) à réceptionner. Ne pas neutraliser le garde de transfert.
- Les workflows #8372/#8382/#8384/#8398 sont des brouillons de preuve temporaires, pas des jobs permanents à fusionner. La couverture globale, l'instabilité, les coûts nets, les skips spécialisés et l'arbitrage métier restent incomplets.

Aucune clôture ni certification de gain net global à cette étape.
