# Contrat d'erreur Kobo-ADEPT / ACSM (P2 #979)

## État
Ce document décrit le **contrat consommé par le runtime Cifereca**, pas une
promesse que `kobo-adept-next` l'émet déjà. Validation fournisseur bloquante.

Le plugin refuse de déduire une cause Adobe depuis `message`, stdout, stderr,
XML, URI ACSM, certificats, jetons ou libgourou en texte libre.

| JSON fournisseur (champ structuré `error.code`) | Code public Cifereca | `retryable` |
| --- | --- | --- |
| `E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER` | `adept_license_already_fulfilled_by_another_user` | `false` |
| `adept_license_already_fulfilled_by_another_user` | identique | `false` |
| `adept_error` (CLI 0.13.6), ou tout code non qualifié associé à exit 10 | `adept_error` | `false` |

Les codes propres à d'autres opérations conservent le traitement historique.
L'erreur générique ne prouve **pas** une activation par une autre identité.
Une ancienne CLI reste compatible ; aucun log brut n'est analysé, même en cas
d'échec.

## Vérification nécessaire en amont

- Confirmer avec `kobo-adept-next` la version d'interface, les codes métier
  réellement émis, et leurs conditions `retryable`.
- Fournir un JSON d'erreur public nettoyé par scénario connu et par version,
  puis étendre les tables de correspondance *exactes* sans déduire de message.
- Le Core reste propriétaire de la projection contextualisée et des actions UI.
  Aucune adaptation Core dans cette PR.
