# Contrat d'erreur Kobo-ADEPT / ACSM (P0 #979)

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


## 1.2.17 — contexte d'opération et expurgation systématique

La classification Adobe s'applique **uniquement** à l'opération
`download request` dont l'entrée structurée contient
`source.type="acsm"`. Une commande `acsm return` est une
**restitution**, et un échec de profil ou un téléchargement HTTP/Kobo
ne peut être interprété comme un problème d'activation ACSM d'après
le seul code retour 10.

Pour un téléchargement ACSM en échec, **tous** les codes fournisseurs
inconnus, structurés ou mal formés, et les anciens retours 10, sont
projetés sur `adept_error` avec message public fixe et
`retryable=false`. La cause `already_fulfilled_by_another_user` n'est
retenue que si le **code machine exact** est présent et que
l'opération d'origine est bien ACSM.

Pour les opérations non ACSM, un exit 10 ou un code Adobe contradictoire
donne désormais `kobo_adept_failed` fixe, **sans** exposer le message
fournisseur. Les autres codes métier et politiques de retry hors ACSM
restent inchangés. Ce durcissement est fondé sur des scénarios
synthétiques et **ne prouve pas** que l'outil externe v0.13.6 expose
les nouvelles causes structurées. Aucune analyse de stderr, URL
signée, XML de licence ou identité Adobe n'est permise ; aucune
modification du Core/UI ni libgourou.
