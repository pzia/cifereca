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


## Réception complémentaire — six shards Core et qualification des mutants

Cette section actualise les constats intermédiaires précédents. Les références A/B historiques restent inchangées.

- Shard 4 reçu : [46114204645](https://github.com/pzia/cifereca/actions/runs/46114204645), job 140884234173, artefact 15252075192; 3 654 pass et un skip dans chacun des quatre passages. Les correctifs de tests sont appliqués symétriquement.
- Consolidation [46115728500](https://github.com/pzia/cifereca/actions/runs/46115728500) : shards 0,2,3,4,5,6, 21 849 → 21 852 pass, 24 skips par bras, identités et statuts plain/coverage identiques, union 173 511 → 173 522 arcs. Somme des temps muraux externes plain 817,86 → 820,94 s; somme des durées pytest plain 812,225 → 817,514 s et coverage 1 539,221 → 1 539,033 s. Chronomètres externes coverage manquants pour cinq shards; aucun gain global démontré.
- Collecte [46115728502](https://github.com/pzia/cifereca/actions/runs/46115728502) : 29 041 → 29 044 nodeids au total, zéro ambiguïté/hors partition; zéro manque ou ajout inattendu sur six shards; cinq skips de collecte sans classname par bras, conservés séparément.
- Recouvrements [46115728533](https://github.com/pzia/cifereca/actions/runs/46115728533) : 19 962 contextes run par bras, 13 152 → 13 146 signatures distinctes, 2 615 groupes répétés par bras (9 425 → 9 431 membres), 219 groupes intermodules par bras (1 025 → 1 027 membres). Les contextes observés ne représentent pas tous les cas collectés. Conserver les oracles distincts de #1012/#1026 (JSON, types, Unicode, modes et troncature), #095/#1143 (compatibilité brute, chemins, logs, exceptions), #3416 (états Sync, priorité de l'erreur, neutralité fournisseur, témoin GTK).
- #8406 fusion `f8243f15afe9bae986871e3e77bbc51347995187`, CI [46115053505](https://github.com/pzia/cifereca/actions/runs/46115053505) et témoin [46115070795](https://github.com/pzia/cifereca/actions/runs/46115070795) : 10 pass sous couverture, mutant d'ouverture de la base source détecté par le garde mémoire. Shard 7 repris [46115259643](https://github.com/pzia/cifereca/actions/runs/46115259643).
- #8409 fusion `e1fecefa3d8d3ef8260e2e32199d83e2e0628ae6`, CI [46115581413](https://github.com/pzia/cifereca/actions/runs/46115581413). Témoin ordonné [46115420138](https://github.com/pzia/cifereca/actions/runs/46115420138) : ancien #4291 passe puis deux #083 échouent par refus d'autorisation, nouveau trois pass et alias restauré, avec et sans couverture. Cause confirmée : import du consommateur sous substitution temporaire du chemin. Aucun garde produit modifié. Shard 1 repris [46115593440](https://github.com/pzia/cifereca/actions/runs/46115593440).
- #8402 Kobo : deux classes de session et deux états du fichier de destination conservés, 10 → 4 cas proposés, mêmes assertions. ABBA [46114794116](https://github.com/pzia/cifereca/actions/runs/46114794116), JUnit 0,129 → 0,057 s (-55,8 %), wall 1,33 → 1,23 s. Quatre mutants identiques tués A/B, dont écriture détectée par les deux seuls cas avec destination préexistante. CI verte [46114723518](https://github.com/pzia/cifereca/actions/runs/46114723518).
- Audit indépendant des archives [46116166518](https://github.com/pzia/cifereca/actions/runs/46116166518) : sept mutants ADEPT/Kobo, 58/58 échecs conformes aux oracles recherchés (12 ADEPT, 46 Kobo), sans erreur de collecte. Les deux candidats restent non fusionnés en attente d'arbitrage métier sur la réduction des répétitions de fixtures fraîches.

### Coût ponctuel de qualification

Instantané documenté dans #8216 avant réception des deux derniers shards : 24 846 secondes de jobs terminés, dont baseline Core 23 596 s (4 884 succès, 3 112 échecs, 15 600 annulations), consolidation 429 s, diagnostics 707 s et candidats 114 s. Mesure REST started_at/completed_at, somme de temps écoulé par job, non facturation ni durée critique. Jobs encore actifs, CI PR courante, autres campagnes initiales/plugins et coût récurrent du contrôle préventif exclus. Les reprises infructueuses représentent 79,3 % de ce sous-ensemble baseline. Privilégier la preuve ciblée; ne pas transformer une campagne d'audit complète en gate permanent.

**Réception non finale** : shards 1 et 7, couverture globale, coûts nets récurrents, skips spécialisés et accords métier restent à recevoir. Aucun quota de suppression, aucun nouveau gate permanent.


## Réception des huit shards — 28 novembre 2026

Les deux derniers shards ont terminé avec succès dans les quatre passages A/B, plain et coverage. Les correctifs d'isolation sont symétriques; les révisions historiques restent celles indiquées plus haut.

| Shard | Preuve / archive | Cas réussis et skips par passage | Durée pytest A plain / coverage | Durée pytest B plain / coverage |
|---|---|---|---|---|
| 1 | [46115593440](https://github.com/pzia/cifereca/actions/runs/46115593440), job 140888588322, artefact 15251836589 | 3 559 + 7 | 123,82 / 246,14 s | 128,70 / 241,51 s |
| 7 | [46115259643](https://github.com/pzia/cifereca/actions/runs/46115259643), job 140887533359, artefact 15251284951 | 3 606 + 1 | 249,05 / 563,73 s | 125,75 / 254,10 s |

Le workflow de consolidation intégrale [46116894778](https://github.com/pzia/cifereca/actions/runs/46116894778), PR #8419, HEAD `eb063a95d8c65d14cbfeb3b6dd7984b6c54425e3`, réutilise les huit archives et la collecte canonique 46115728502. Au contrôle intermédiaire, inventaires, patches symétriques (1/4/7), multiensembles JUnit et projection des nodeids canoniques ont passé leurs étapes respectives : A 29 014 réussis + 32 skips, B 29 017 + 32; 29 041 / 29 044 cas collectés (cinq skips de collecte hors nodeids par bras). Recouvrements et motifs exacts des skips restent à réceptionner. Ne pas présenter le job comme terminé avant son verdict. Aucun nouveau passage pytest n'a été lancé. Les fortes différences de durée du shard 7 ne permettent pas d'attribuer le gain apparent aux seules modifications : ordre fixe A puis B, charge runner non contrôlée; les chronomètres externes coverage manquent encore pour cinq shards historiques.

### Pont vérifié vers main

Comparaisons GitHub A→B (9 commits, 13 fichiers) et B→main épinglé `41d29f82a58ed935ae97a29c34b4e3f078ea8ee5` (19 commits, 13 fichiers) : pas de modification de src/cifereca ni du runtime des plugins. Trois fichiers de tests plugins modifiés, six correctifs d'isolation Core, outillage/documentation et deux fichiers de backlog hors périmètre.

Les six blobs Core de contrôle du harnais shard 1 correspondent à ce main : `b8c2f002c4cc74f20052ddb943d19ba5a3a62198`, `3740785a046c49370e72c0b3430b9f1db50c90ae`, `5f6b325ff81b8063d0b505ab6194a4cac1d905cc`, `06233dd72522531c934491c1399d8f5d356abdf9`, `a8b5006eb8a1100d2fdc7c13da51dda80e4f274c`, `272434a0ab304fc832bb3013e31e912047079664`. Les trois blobs de tests plugins correspondent aux HEAD fusionnés #8270, #8299 et #8375, CI exactes [46063453198](https://github.com/pzia/cifereca/actions/runs/46063453198), [46063950220](https://github.com/pzia/cifereca/actions/runs/46063950220), [46067390174](https://github.com/pzia/cifereca/actions/runs/46067390174) vertes. Journaux ciblés : Kobo 1 629 réussis, Numilog après second lot 2 398 réussis et deux skips. Les 21 autres suites plugins n'ont pas de diff propre A→main dans ce périmètre. Ce pont n'est ni une nouvelle exécution instrumentée sur main, ni une recette des modifications backlog.

### Gains locaux appariés, métrique homogène

Relecture des journaux des quatre campagnes ABBA; moyennes arithmétiques des deux durées **affichées par pytest** par bras, en secondes, sur les modules concernés. Pour #8238, retenir le mode séquentiel seul dans la somme; l'alternative xdist (138,205 → 28,055 s) n'est pas un gain supplémentaire.

| Lot fusionné / preuve | Cas par passage | A1 / A2 | B1 / B2 | Moyenne A → B | Réduction locale |
|---|---:|---:|---:|---:|---:|
| #8238 [46059532585](https://github.com/pzia/cifereca/actions/runs/46059532585), job 140710141005 | 30 | 139,08 / 136,79 | 27,84 / 27,91 | 137,935 → 27,875 | 110,060 s |
| #8270 [46063477794](https://github.com/pzia/cifereca/actions/runs/46063477794) | 39 | 2,10 / 2,01 | 0,26 / 0,25 | 2,055 → 0,255 | 1,800 s |
| #8357 [46065197369](https://github.com/pzia/cifereca/actions/runs/46065197369) | 14 | 0,69 / 0,66 | 0,39 / 0,39 | 0,675 → 0,390 | 0,285 s |
| #8375 [46067082676](https://github.com/pzia/cifereca/actions/runs/46067082676) | 24 | 2,41 / 2,28 | 0,43 / 0,42 | 2,345 → 0,425 | 1,920 s |
| Somme descriptive | 107 | — | — | 143,010 → 28,945 | 114,065 s |

Ces quatre lots conservent leurs identités et résultats sur chaque passage. La somme décrit quatre expériences locales distinctes, pas un pipeline ni un gain de temps facturé. Elle n'inclut ni candidats non fusionnés #8393/#8402 ni réduction par suppression SourceKey. L'instrumentation et le coût d'audit ponctuel sont distincts du coût de fonctionnement récurrent. Le contrôle qualité incrémental a réussi sur sept PR (runs 46116325037, 46115581413, 46115053505, 46114723518, 46114601789, 46114073006, 46068401908), durées de l'étape 1/0/0/0/0/0/0 s à résolution d'une seconde; zéro ne signifie pas gratuit et l'échantillon ne mesure pas tous les changements possibles.

**Réception restant ouverte :** verdict complet et artefact #8419, éventuels écarts d'arcs et motifs des skips, preuve de gain net global ou décision explicite de limiter la réception aux gains locaux, avis métier sur #8393/#8402. Les 58 échecs JUnit qualifiés concernent sept mutants ADEPT/Kobo distincts. Aucune clôture ni suppression supplémentaire.


## Réception consolidée de #8419

[Run 46116894778](https://github.com/pzia/cifereca/actions/runs/46116894778), job 140892639402, terminé success. Archive [15252455936](https://github.com/pzia/cifereca/actions/runs/46116894778/artifacts/15252455936), 612 234 octets, SHA256 `95dfc55f9814197b349e9bf595337b792207261b2e267cc6a4c8406d74c198fd` vérifié après téléchargement; les quatre JSON ont été relus. Cette section remplace le statut provisoire de la précédente.

| Mesure Core historique corrigée | A | B |
|---|---:|---:|
| Nodeids canoniques | 29 041 | 29 044 |
| Réussis | 29 014 | 29 017 |
| Skips de cas collectés / de collecte | 27 / 5 | 27 / 5 |
| Arcs observés (union) | 187 419 | 187 427 |
| Somme temps muraux externes plain (s) | 1 192,26 | 1 076,44 |
| Somme temps suite pytest plain (s) | 1 185,074 | 1 071,936 |
| Somme temps suite pytest coverage (s) | 2 349,066 | 2 034,621 |
| Contextes run enregistrés | 26 495 | 26 495 |
| Signatures d'arcs distinctes | 17 369 | 17 366 |
| Groupes répétés / membres | 3 489 / 12 615 | 3 488 / 12 617 |
| Groupes intermodules / membres | 357 / 1 887 | 358 / 1 891 |

Zéro erreur/échec, collision inter-shards, nodeid manquant, inattendu, ambigu ou hors partition; multiensembles identité/statut plain et coverage identiques par bras. Seul delta de cas : un retrait SourceKey (#8256) et quatre ajouts de tests qualité. Aucun arc A-only sur sources byte-identiques, huit B-only : library_inspection_cache.py +1, notice_c_work_runtime.py +1, provider_sessions.py +2, diagnostics.py +2, task_runtime.py +2. Aucun chemin d'arc non mappé. Ce n'est pas un taux de couverture des branches possibles, ni une preuve d'équivalence ou d'exécution de tous les contextes.

Les 32 skips par passage ont mêmes identités, types et motifs (détail conservé). Cinq de collecte, huit GTK/Xvfb, cinq namespaces bubblewrap, trois gi, trois pymupdf, deux archives plugins, trois opt-ins Chromium/réseau/proxy/CDN, un affichage complet Xvfb/DBus, un E2E dédié, un test installation ciblée. Ne pas les assimiler à des contrats exercés.

Le temps mural plain observé baisse de 115,82 s; hors shard 7 il **augmente** de 7,95 s (942,23 → 950,18). Le temps suite coverage hors shard 7 baisse de 4,821 s (1 785,350 → 1 780,529). Temps externe coverage global indisponible. Ordre fixe et charge runner empêchent d'attribuer le delta global observé aux changements; les quatre ABBA ciblées restent les preuves causales locales. Aucune économie facturée ni gain net global établi.

### Triage des recouvrements supplémentaires

- Plus grand groupe (154 contextes, 54 arcs par signature) : #976/#7244. Les matrices se recouvrent sur huit coordonnées plates et quatre caractères de contrôle; #976 couvre aussi statut/licence et longueur avec suffixe FORGED, #7244 general l'absence de mutation de toute la ligne et drapeaux de non-autorisation, #7244 historical les preuves imbriquées, l'absence d'état fournisseur vérifié et des lignes malformées. Conserver pour l'instant : une fusion éventuelle doit préserver la matrice distincte et les oracles, avec revue métier et mutation; le coût individuel n'est pas établi par la signature.
- #5056 : prix absent/invalide, droit historique, achat descriptif et support numérique/physique ne remplacent pas les refus d'attestation de possession ni les champs identité/compte/révision. #5073 : conserver les classes booléen/None/entier, fournisseurs fictifs, source étrangère/périmée et absence de lecture du ledger. #1120 : conserver les flags de retry invalides et le refus des routes retirées avant I/O. Aucune suppression supplémentaire justifiée.

La consolidation Core est reçue; les accords métier ADEPT/Kobo, la recette des prérequis spécialisés, l'instabilité à long terme et le gain net global restent non établis. Le P0 demeure ouvert.

## Recettes spécialisées et correction du harnais ABA — 28 novembre 2026

**30 des 32 enregistrements JUnit ignorés** dans la campagne Core historique sont désormais rapprochés d'une preuve réussie. Un skip de collecte désigne un module, pas un cas individuel; ne pas additionner ces enregistrements au nombre de tests réussis. Les preuves historiques ci-dessous ont des fichiers de tests identiques au main de contrôle, pas nécessairement un environnement ni un arbre produit identiques.

| Périmètre | Enregistrements rapprochés | Preuve |
|---|---:|---|
| Bubblewrap et installation venv | 6 | [40828541509](https://github.com/pzia/cifereca/actions/runs/40828541509), job 123297125073 : six méthodes unittest effectivement ok, blob `7e58ae7f74133e383be62422bdd9748f95bd4f10`. Ne pas confondre avec le passage headless ultérieur 46 pass/2 skips. |
| PDF | 3 | [45000230952](https://github.com/pzia/cifereca/actions/runs/45000230952), job 137139936411 : lecteur réel positif, chiffré et révocation tardive, 3 pass sans skip, blob `11610c9bc3e8766eed977ee0aba8e4458aebfb2d`. |
| Chromium/Browser Recorder | 9 | [46048444864](https://github.com/pzia/cifereca/actions/runs/46048444864), jobs 140673461630/140673461865 : cinq modules de collecte + proxy/CDN + sessions/restart; neuf fichiers comparés à main (blobs identiques). |
| Provisionnement Chromium Core neuf | 1 | [40391324802](https://github.com/pzia/cifereca/actions/runs/40391324802), job 121812753725, étape 7 : consentement, installation et vrai lancement, 1 pass en 10,12 s, blob `fb2f61fd2de34b994586462ff89da6df24f660c2` identique à main. |
| GTK natif : session exacte, projection des prêts, BnF | 10 | [46117566655](https://github.com/pzia/cifereca/actions/runs/46117566655), main épinglé `9bfd3458d61dbb562bf02bcdc0d8958814ee689a`, job 140894697417 : 10 pass, 1 fail, aucun skip; archive 15252233463. |
| GTK natif : callback prêts A→B→A | 1 | [46117702120](https://github.com/pzia/cifereca/actions/runs/46117702120), job 140895108599 : correctif #8424 exact, 1 pass; mutant suppression de la garde des epochs, 1 fail attendu sur `assert applied == []` (publication de PRIVATE stale loan), aucun skip ou erreur de collecte. |

Le premier passage GTK a révélé un témoin obsolète, non une régression produit établie : `done` reçoit le couple `(rows, cover_rows)`, mais le test historique lui transmettait une liste. #8424 aligne les deux payloads et conserve le refus stale/acceptation fresh; il vérifie en plus que la projection de couverture n'est pas écrasée par une réponse obsolète. La mutation locale sur le runner (jamais sur main) confirme que l'oracle détecte une publication interdite et non une erreur d'unpacking. Archive 15251828186, SHA256 `72a2fd619f34b1210447e13b9b6f9db590fe5a2fa462aad2ea2af05f213086d8`. CI [46117680389](https://github.com/pzia/cifereca/actions/runs/46117680389) verte sur HEAD `97b1aa2b3d0c5fe9b0e71ff3bee1e22a03b26565`; squash `67d5891598bea6761277490771887076c006aaca`, blob main `802622f8f94bded4f964920c2a530f5cdc784e30` revérifié. Aucun changement de code produit.

**Deux skips restent non reçus** : `test_core_ui_095.py::test_plugins094_manifest_and_activation` et `test_public_identity_activation`. Le répertoire par défaut `tests/fixtures/plugins-0.9.4` est absent (404, aucun commit pour ce chemin dans l'historique main consulté); les tags de releases testés v0.9.4, 0.9.4, plugins-0.9.4 sont introuvables. Le second test demande spécifiquement Gutenberg 1.1.0. Ne pas substituer des archives actuelles à ces fixtures ni supprimer les tests sur la seule absence de données. Leur conservation ou migration nécessite qualification du contrat de compatibilité historique avec le rôle propriétaire Plugins. Aucun gate désactivé.

Restent aussi les accords sémantiques #8393/#8402, l'instabilité à long terme et le bilan net global. Les 30 enregistrements rapprochés ne signifient pas une exécution exhaustive de main dans un seul environnement.


## Réception nette ciblée et arbitrages propriétaire — 28 novembre 2026

Cette section actualise les statuts provisoires ci-dessus, sans assimiler le périmètre mesuré à une recette globale. A historique épinglé `4fd07df843b9d940746bbfe962a1877eec15d431`, B main épinglé `246dc0125e8786d90e259520cc58ed5ccb90e350`. [#8428 / run 46118081253](https://github.com/pzia/cifereca/actions/runs/46118081253), HEAD `24c53fd824198e27238047435ee50afdd22e622e`, job 140896262898. Un runner et environnement partagé, arbre propre au même chemin, ordre A1/B1/B2/A2, processus Core/Numilog/Kobo distincts. Durées externes des sous-processus, B inclut quatre nouveaux tests du détecteur et une analyse incrémentale conservatrice de tout le diff A→B, hors installation et coûts non récurrents. Périmètre : quatre modules Core modifiés, deux modules Numilog, un module Kobo, plus le module qualité en B.

| Mesure (secondes) | A1 | B1 | B2 | A2 |
|---|---:|---:|---:|---:|
| Core | 143,055 | 29,967 | 29,995 | 140,907 |
| Numilog | 3,738 | 0,817 | 0,806 | 3,709 |
| Kobo | 0,559 | 0,556 | 0,549 | 0,551 |
| Scanner incrémental | — | 0,278 | 0,276 | — |
| Total externe | 147,351 | 31,619 | 31,626 | 145,167 |

Moyennes 146,259062 → 31,622439 s : **114,636624 s économisées, 78,38 %**, scanner inclus (0,277188 s moyen). Deux paires de même signe (115,732673 et 113,540574 s); deux observations par bras, pas d'intervalle de confiance robuste. La somme des ABBA des lots précédents n'est pas additive avec cette mesure. Aucun gain du pipeline complet ou en facturation établi, aucune extrapolation à l'instabilité à long terme. Les deux candidats ADEPT/Kobo approuvés ultérieurement ne figurent pas dans B.

### Réconciliation indépendante de l'archive, sans rejouer les tests

Le workflow expérimental est rouge : après les quatre passages réussis, son assertion de delta Kobo strict a rejeté dix changements de noms paramétrés. Conserver ce verdict, ne pas le présenter comme une CI verte. Archive [15252103788](https://github.com/pzia/cifereca/actions/runs/46118081253/artifacts/15252103788), 27 146 octets, SHA256 `41eed26196e32697d0a1789ba3b8c1ed6324480b05a6c5b6debd0810d35375ce` recalculé. Les douze XML ont été parsés indépendamment : multiensembles (classname, name, statut) identiques A1/A2 et B1/B2, tous pass, aucun skip/erreur. Core 51→54 (retrait SourceKey attendu et quatre tests du détecteur), Numilog 63→63 identiques, Kobo 64→64. Total 178→181. Pour les deux fonctions Kobo ci-dessous, les paramètres d'entrée sont inchangés, le nouveau paramètre est un oracle explicite. Blobs A `57992594fbb4038cb5298cb0dfc432ea8a0e4f91`, B `228a583ac73cb02eab15cc31b781ea843b3e9e7b` (#8299). Vérification du multiensemble des dix anciens noms transformés en dix nouveaux noms, même classe/fonction/statut, zéro autre delta. Ne pas normaliser les identités de manière générale.

| Fonction | Entrée inchangée | Valeur attendue ajoutée |
|---|---|---|
| test_benign_percent_encoded_cover_paths_remain_valid | https://cdn.example/cover%20name.jpg | identique |
| idem | https://cdn.example/cover%2520name.jpg | identique |
| idem | https://cdn.example/cover%25name.jpg | identique |
| idem | https://cdn.example/cover%GG.jpg | identique |
| idem | /covers/book%20name.jpg | https://vault.example/covers/book%20name.jpg |
| test_safe_nested_images_remain_usable_after_route_guard | covers/edition/cover.jpg | https://vault.example/base/covers/edition/cover.jpg |
| idem | /covers/edition/cover.webp | https://vault.example/covers/edition/cover.webp |
| idem | https://cdn.example/book/cover%20image.jpg | identique |
| idem | https://cdn.example/covers/cover%2520image.jpg | identique |
| idem | https://cdn.example/covers/book%25variant.jpg | identique |

Les deux JSON du scanner donnent les mêmes 16 fichiers, 157 définitions, zéro erreur base/HEAD et un nouveau signal `tests/test_backlog_compaction.py::Tests.test_xl_group_pilot_and_twenty_members` : `no_recognized_assertion`. Revue de source : délégation via boucle/subTest à `exercise_xl_group`, oracles réels de refus, absence d'écriture, snapshots, reprise, compactage et purge. Faux positif de reconnaissance syntaxique, pas suppression de test; conserver la limite explicite du détecteur, sans élargir une exemption automatique.

CI générale du brouillon [46118081203](https://github.com/pzia/cifereca/actions/runs/46118081203) : 29 019 pass, 29 skips, deux échecs exacts des gardes de publication d'artefacts (workflow temporaire hors liste autorisée). Même nature pour #8419 et #8423. Ne pas fusionner ces workflows ni modifier les gardes.

### Coûts ponctuels dédupliqués

Inventaire des 36 runs / 49 jobs précédemment recensés, REST started_at→completed_at, dernière tentative disponible par run, hors autres campagnes non inventoriées. Baseline 25 593 s, consolidation 870 s, diagnostics 793 s, candidats 114 s, CI connexes 891 s = 28 261 s (8 699 succès, 3 843 échecs, 15 719 annulations). Ne pas additionner les anciens instantanés. Ajouter les trois jobs désormais terminés : #8428 preuve 386 s (verdict failure malgré passages réussis), CI #8428 308 s (failure), CI exacte ADEPT 37 s (success). Sous-total 52 jobs / 39 runs, 28 992 s (8 h 03 min 12 s), dont 8 736 s succès, 4 537 s échecs, 15 719 s annulations. Ce sont des temps écoulés par job, pas des minutes facturées, ni le chemin critique; les tentatives antérieures et les autres campagnes ne sont pas exhaustivement recensées.

### Arbitrages et intégration séquentielle

[Arbitrage utilisateur](https://github.com/pzia/cifereca/issues/8216#issuecomment-7235556166) : accepter les deux rationalisations, conserver les tests historiques 0.9.4. ADEPT #8393 : 14→12 cas, trois refus consécutifs et oracles après chacun conservés; trois mutants identiques tués A/B. Réduction des répétitions de fixtures fraîches, non équivalence statistique d'instabilité. HEAD actualisé `44d470e5bda77499d1767fe2678a3a963abcf4a7`, [CI 46118574319](https://github.com/pzia/cifereca/actions/runs/46118574319) verte, squash `d374bd3b47badf341e7554c64019a20c82438d3c`, blob main `fb0fe681689656dec7e438dadfbc02a9bfd6553b` revérifié. Kobo #8402 : accord acquis, HEAD actualisé `88b778a7f50bf99384797c4735a6ffc61dd854f7` sur ce main, blob candidat `9d0c2d8d523173f2a543115fff6e84e62e1511c1` inchangé, [CI 46118667686](https://github.com/pzia/cifereca/actions/runs/46118667686) encore en cours au contrôle; fusion interdite avant verdict exact. Les deux skips historiques restent explicitement non reçus : 14 releases accessibles sans les ZIP requis, Gutenberg source 1.2.21 singleton_public ne remplace pas l'archive 1.1.0; compatibilité du parseur installé v1 maintenue. Aucun abandon du contrat.

Les brouillons de preuve #8419/#8423/#8416/#8382/#8372 ont été fermés sans fusion après capitalisation sur main, branches et archives non supprimées, rétention GitHub inchangée. Restent la réception Kobo, le suivi d'instabilité à long terme, la preuve globale non confondue ou un arbitrage explicite sur le périmètre local, les deux archives historiques et la cadence de maintenance. Le P0 reste ouvert.
