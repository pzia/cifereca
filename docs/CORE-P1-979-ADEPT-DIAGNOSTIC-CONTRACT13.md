# #979 — conserver les codes Adobe/ADEPT 1.3 dans Diagnostics Core

Le runtime installé `runtime.kobo-adept` valide déjà les six
`error.code` structurés du contrat [1.3](../src/plugins/runtime-kobo-adept/ERROR-CONTRACT.md).
Cependant, le second filtre de confiance
`core/diagnostic_adept.py::ERROR_CODES` ne reconnaissait que
`adept_error` et quelques causes historiques : cinq causes pourtant
déjà qualifiées par le runtime étaient rétrogradées en
`code_non_disponible` dans les événements Core et les logs applicatifs.

## Correction livrée

Le schéma Core conservant l'attestation **de l'identité du plugin et du
package installé** reconnaît désormais, sans élargir le format ni
analyser un texte libre :

| Code machine exact | Signification prudente |
| --- | --- |
| `invalid_acsm` | Jeton ACSM déclaré invalide par la CLI |
| `profile_not_found` | Profil explicitement absent |
| `adept_profile_required` | Profil ADEPT requis |
| `adept_profile_error` | Erreur de profil signalée |
| `adept_profile_incompatible` | Profil déclaré incompatible |
| `adept_error` | Erreur ADEPT générique, cause Adobe inconnue |
| `adept_license_already_fulfilled_by_another_user` | Cause spéciale historique uniquement sur code structuré normalisé |

### Protections inchangées

- Aucune interprétation du seul `exit_code=10`, des
  `stdout/stderr`, du XML, des URL ACSM, messages fournisseurs, profils
  ou traces d'exception.
- Codes hors liste (notamment l'alias brut
  `E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER`, qui doit être
  **qualifié/normalisé par le runtime**) : `code_non_disponible`
  et aucune indication `retryable`.
- Drapeau de réessayabilité accepté **uniquement** s'il est booléen natif
  sur une cause structurée qualifiée ; ce champ est une observation, pas
  une commande de nouvelle tentative. Le fulfillment ACSM garde sa
  politique prudente côté runtime.
- Aucune nouvelle lecture de compte, nouvelle exécution ADEPT,
  réactivation d'une licence ou restitution volontaire.
- Le nom du module, la catégorie et le fournisseur canonique ne sont
  jamais déduits des messages d'exception.

Les nouveaux tests simulent le runtime installé et le canal de
Diagnostics, la sérialisation canonique, la confidentialité, le
`returncode=10` non qualifiant et les cas de réponse malformée.

## Encore ouvert

Ce correctif ne prouve **pas** que la CLI externe
`kobo-adept-next` émet les codes avec la bonne sémantique. Il ne
restaure pas non plus une cause ADEPT éventuellement déjà perdue
par une autre façade Core avant le journal, ni n'ajoute une commande
de réessai. La qualification amont et les conseils UI contextualisés
restent à traiter dans #979, après la fenêtre P0 #3723.
