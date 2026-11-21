# #979 — Réception des preuves ADEPT 1.3 / 21 novembre 2026

## Périmètre et autorité

Contrat consommateur [CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md](../src/plugins/runtime-kobo-adept/CIFERECA_KOBO_ADEPT_CONTRACT_1.3.md) §4, baseline Kobo-ADEPT 0.13.5, contract_version 8 ; version 0.13.6 observée dans les incidents historiques. Le contrat décrit les champs structurés, pas l'émission effective de chaque branche de la CLI installée. Les PR #3659, #3661, #3677, #4260 et #4262 ont déjà livré transport, projection et aide opérateur ; ne pas les réimplémenter.

| Code reçu sous forme structurée | Projection consommateur | Preuve d'émission réelle disponible |
| --- | --- | --- |
| invalid_acsm | identique | unknown |
| profile_not_found | identique | unknown |
| adept_profile_required | identique | unknown |
| adept_profile_error | identique | ancienne capture 0.13.6 : loans list sans --profile |
| adept_profile_incompatible | identique | unknown |
| adept_error | identique pour opération ADEPT qualifiée | captures 0.13.6 : deux download request, exit 10, cause précise non confirmée |
| E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER | alias legacy qualifié uniquement pour ACSM explicitement attesté | un diagnostic libgourou direct historique, **pas** preuve d'émission structurée par la CLI |
| Code absent ou inconnu, exit 10 | adept_error si opération ACSM attestée ; kobo_adept_failed sinon | cause Adobe précise unknown |
| Réponse contradictoire | kobo_adept_invalid_response | tests synthétiques, pas de cause Adobe |

Sources : [#979](https://github.com/pzia/cifereca/issues/979) et [qualification du 03/11](https://github.com/pzia/cifereca/issues/979#issuecomment-6596543940). Aucun détail d'incident individuel ni aucune sortie privée reproduite.

## Retry

Le booléen natif d'une erreur structurée connue est conservé hors fulfillment. Un fulfillment ACSM ambigu ne devient jamais réessayable par ce seul booléen, ni par l'exit 10. La décision de pause, reprise, acquisition et mutation demeure sous #4496 ; ce document ne modifie aucun garde.

## Réception

La recette de cette tranche est un processus synthétique injecté dans le vrai parseur runtime, puis passage par le contexte plugin installé et les deux canaux Core. Elle complète les recettes séparées déjà présentes. Elle ne prouve aucune émission côté fournisseur. Le dépôt externe kobo-adept-next n'est pas accessible via la connexion GitHub utilisée pour cet audit. Ne pas déclarer les codes externes reçus ni ajouter de correspondance à partir de stderr, de XML ou d'un nom de classe. L'échantillon amont manquant reste explicitement unknown ; aucun appel réel ni chantier externe requis dans cette tranche.
