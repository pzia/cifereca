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
