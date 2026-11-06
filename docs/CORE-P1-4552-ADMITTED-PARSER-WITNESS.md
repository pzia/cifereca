# P1 #4552 — témoin de parsing épinglé à une notice source admise

## Contrat Core strictement inerte

Cette tranche relie les deux fondations déjà fusionnées :
- `NoticeSourceObservation` (#4751), enveloppe d'observation brute et scope
  fournisseur/branche/record/édition/compte/identité ;
- `ParseWitness` (#4753), comparaison hors réseau des entrées strictement
  identiques ou effectivement changées.

`admitted_source_scope_sha256(key, parser_id)` produit un SHA256 structuré
incluant **l'identité, le credential, le compte, la branche, la notice,
l'édition, le rôle de manifestation, le fournisseur, la visibilité et le
parseur**. La version du parseur n'entre pas dans cette clé, afin de permettre
le contrôle de régression après une mise à jour. Aucune coordonnée privée
n'apparaît dans la sortie opaque.

`review_admitted_source_parse_witness` exige la classe exacte
`NoticeSourceObservation`, puis **réadmet** le JSON/descripteur avec le
contrat Core en vérifiant le contenu JSON canonique et la révision attendue.
L'altération d'un champ du snapshot ou la substitution de JSON est refusée,
pas qualifiée comme régression.

Une baseline approuvée liée au même scope et au SHA réel des octets d'entrée
peut donner `parser_regression_confirmed` si le nouveau parseur perd un
champ critique sur **les mêmes octets**. Autre réponse : `source_changed`.
Page partielle, autre profil/compte/édition/parseur, baseline non approuvée :
`unverified`. Codes des champs sous forme `critical_field_N` uniquement.

## Garanties et limites

Le module ne vérifie **pas** l'authenticité des octets transmis au parseur :
le Core appelant doit calculer `input_sha256` sur les octets d'une réponse
déjà autorisée et sur la source réellement identifiée. Un SHA déclaré
directement par un plugin n'est pas une attestation. L'admission des objets
n'accorde pas un droit de charger un contenu, de parser un compte privé,
de rejouer une requête ou de publier une Notice C.

Aucun GET/POST, accès SQLite, transaction, mutation, droit, quota, transfert,
callback ni événement GUI. À réaliser ensuite sous #4552/#2385/#3724 :
provenance authentifiée des parseurs/fixtures, baseline approuvée persistée,
ordonnancement local, comparaison autorisée en CI/worker et signalement
coalescé sans remplacer la dernière notice saine.

Tests synthétiques : même source et régression, public/privé,
A→B/credential/compte/branche/édition, JSON ou révision altérés, réponse
partielle/source changée, absence de fuite d'identifiants dans les résultats.
