# #2845 — Matrice des invocations ADEPT et verdict de non-coalescence

## Capture historique du 22 octobre 2026

La source disponible est le comptage agrégé de [#2845](https://github.com/pzia/cifereca/issues/2845), pas les onze événements individuels. La classification ci-dessous conserve cette limite. #3044 établit depuis la distinction activation/sonde Core, sans prouver la corrélation rétroactive.

| Opération | Nombre historique | Consommateur possible d'après le code/les travaux reçus | Portée et fraîcheur historiques | Décision |
| --- | ---: | --- | --- | --- |
| contract | 2 | activation ; core_probe | attribution unitaire inconnue | conserver |
| capabilities | 2 | activation ; core_probe ; préflight frais | attribution unitaire inconnue | conserver |
| profile list | 4 | navigation/profils ; synchronisation ; préflight | identité, génération et consommateur inconnus | conserver |
| loans list | 3 | rapprochement ou contrôle des prêts | profil exact et phase inconnus | conserver |
| Total | 11 | | | 0 suppression supplémentaire démontrée |

Les durées publiées pour les profils étaient environ 186–232 ms, pour les prêts 289–348 ms. Aucune somme exacte reconstituable à partir de ces intervalles.

## Captures ultérieures distinctes

- 29/10 : 22 invocations terminales documentées dans [CORE-P1-2845-ADEPT-BATCH-CAPABILITY.md](CORE-P1-2845-ADEPT-BATCH-CAPABILITY.md).
- 10/11 : 17 commandes réussies ; 8 profile list, 2 contract, 3 capabilities, 1 loans list et 3 download request. Les téléchargements ne sont pas des doublons.
- 21/11 : 50 commandes réussies ; 38 profile list (8 430 ms cumulées), 4 loans list (1 177 ms), 5 capabilities, 2 contract et 1 download request (6 129 ms). Les 42 lectures d'inventaire sont unattributed sur le worker d'ouverture. Total inventaire mesuré : 9 607 ms cumulés, pas une latence utilisateur. Une partie participe à la vérification fraîche avant GET puis fulfillment ; ne pas les supprimer. Source : [mesure terrain 21/11](https://github.com/pzia/cifereca/issues/2845#issuecomment-7058562153).

Ne pas soustraire deux captures de versions et de parcours différents. Un même read_scope_ref identifie seulement une configuration/commande ou un profil de runtime dans une instance ; il ne prouve pas l'identité, l'epoch, la fraîcheur, l'exhaustivité, le résultat ou l'équivalence des appelants.

## Mesure synthétique sur le runtime existant

La recette test_adept_11_read_invocations_p0_2845 utilise le vrai runtime et un transport subprocess substitué avant toute exécution. Elle reconstruit les quatre familles de commandes dans les proportions historiques, sans prétendre reproduire les onze appels terrain. Les quatre profile list de même scope retournent vide, non vide, échec, vide. Les trois loans list A/B/A distinguent B de A et changent de contenu lors du second A. Deux preflight_acsm_profile consécutifs de même scope voient une capacité disparaître.

| Scénario | Nombre actuel attendu | Suppression supplémentaire justifiée |
| --- | ---: | ---: |
| Activation + sonde Core | 4 | 0 |
| Inventaire profils, évolution et erreur | 4 | 0 |
| Inventaire prêts A/B/A | 3 | 0 |
| Préflights séparés avec readiness modifiée | 2 | 0 |

L'optimisation déjà reçue #3663 (capabilities 3→1 dans le même lot, mêmes résultats, lectures fraîches conservées) demeure la seule réduction chiffrée démontrée dans ce périmètre. Pas de gain terrain revendiqué. La recette vérifie les nombres, les catégories connues, les références de portée et la conservation des erreurs ; elle ne mesure pas les temps du vrai processus Adobe.

## Suite strictement utile

Aucune modification du fichier runtime partagé avec #7236 Publication. Après libération de ce fichier, attribuer les consommateurs encore unattributed au point d'entrée exact et reporter la phase (préflight GET/fulfillment, rapprochement, dashboard/refresh explicite). Ne pas inférer de phase depuis argv, durée ou proximité temporelle. Recompter une même recette instrumentée avant/après, sans demander de nouvel emprunt ou fulfillment. Si aucune paire n'a de fenêtre de fraîcheur et d'epoch équivalentes, verdict de non-optimisation recevable. Aucun cache supplémentaire ni nouvelle autorité ADEPT.


## Attribution statique complémentaire — 21/11/2026

Audit de main (les liens désignent les symboles, non une attribution d'événement terrain).
La PR Publication #7236 est fusionnée ; aucune modification des fichiers partagés
dans cette tranche. Le runtime `profiles()` exécute une commande par appel et
`_loan_inventory()` distingue explicitement le nom de profil et l'opération.
Les catégories actuelles ne suffisent pas à distinguer les consommateurs suivants :

| Catégorie à instrumenter | Point d'entrée attesté | Fenêtre et frontière |
| --- | --- | --- |
| dashboard_opening | `core/sync_coordination.py`: boucle identités → `adept_profiles_for_identity` → `_adept_profile_row` → `kobo_profiles` | Inventaire pour chaque identité liée du cycle ; erreur lookup distincte d'un inventaire vide |
| source_runtime_inspection | `services.py:_inspect_source_adept_runtime` → `kobo_profiles` | Lecture explicite, état local avant/après identique exigé |
| profile_choices | `services.py:adept_profile_choices` → `kobo_profiles` | Liste explicite de sélection ; pas de partage avec un contrôle d'exécution |
| execution_profile_resolution | `services.py:resolve_adept_profile_for_identity` → `kobo_profiles` | Résolution fraîche de la référence ; chemin existant susceptible de mettre à jour un binding historique, hors périmètre diagnostic |
| origin_profile_resolution | `services.py:_originating_adept_profile_id` → `adept_profiles.originating_profile_id` | Lié à l'origine attestée de l'identité ; ne pas inférer depuis le profil actif |
| reconciliation_candidates | `services.py:adept_inventory_candidates_for_acquisition` → `_adept_profile_row` | Cache appelant existant, clé identité + scope + références uniques ; ne pas partager entre cycles |
| loan_choices | `services.py:adept_loan_choices` → `kobo_profiles`, puis `runtime.loan_inventories` | Consultation explicite, par ensemble de profils candidats ; réponse partielle distincte d'absence |
| loan_attach_revalidation | `services.py:attach_adept_loan` → `kobo_profiles`, puis `runtime.loans` | Revalidation sous verrou d'une action utilisateur existante ; hors périmètre de mutation ADEPT |
| acsm_fresh_preflight | `core/adept_runtime_preflight.py` → `runtime.preflight_acsm_profile` | Capabilities fraîches, indépendantes du cache descriptif |
| acsm_execution_preflight | `runtime.download` → `preflight_acsm_profile` | Nouvelle capacité fraîche immédiatement avant la commande ; ne pas fusionner avec préflight Core |
| core_probe | `runtime.probe_core` → `contract`, `capabilities` | Catégorie déjà explicite, cache descriptif distinct dans `acquisition_capabilities` |

Points de lecture vérifiés dans
[`services.py`](https://github.com/pzia/cifereca/blob/main/src/cifereca/services.py),
[`sync_coordination.py`](https://github.com/pzia/cifereca/blob/main/src/cifereca/core/sync_coordination.py),
[`adept_runtime_preflight.py`](https://github.com/pzia/cifereca/blob/main/src/cifereca/core/adept_runtime_preflight.py)
et [runtime](https://github.com/pzia/cifereca/blob/main/src/plugins/runtime-kobo-adept/cifereca_kobo_adept_runtime.py).

La classification des 42 lectures terrain reste inconnue. Noter notamment que
`adept_profiles_for_identity` restreint la projection retournée, mais appelle
indirectement la commande d'inventaire complet : le command scope seul ne
distingue donc pas deux identités. La boucle des références dans
`adept_inventory_candidates_for_acquisition` constitue une **hypothèse**
de répétition intra-opération, pas une mesure : vérifier d'abord le nombre
de références distinctes, la génération, les changements d'état ou erreurs
entre lectures et les droits de chaque appelant. Ne pas élargir le cache existant.

Réception du lot : l'audit statique permet de définir les points de mesure,
pas de déclarer les onze invocations historiques identifiées ni d'attribuer
les 38 appels profile list du 21/11. Garder le verdict de zéro suppression
supplémentaire démontrée ; une instrumentation contractuelle future devra
utiliser des catégories fermées sans coordonnées privées et être coordonnée
avec Publication avant édition de fichiers partagés.
