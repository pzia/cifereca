# P1 #976 — empreinte d'observation locale avant validation ADEPT

## Pourquoi

Les commandes `audit-origin`, `audit-canonical`, `audit-transaction`
et `preview-resolution` réunissent des **observations locales** qui peuvent
changer entre une prévisualisation et une éventuelle décision ultérieure.
Même si un couple `SourceReference/OriginLink` reste présent, la **référence
exacte du reçu Adobe** peut avoir changé ; un simple compteur de liens ne
permet pas de détecter cette modification.

La primitive Core
`snapshot_paris_adept_local_evidence(storage, loan_id, item_id)`
génère une empreinte `sha256:paris-adept-local-evidence-v1` des données
historiques et canoniques **persistées**, strictement sur le prêt Paris
et l'item désignés.

L'entrée privée de hachage contient notamment les reçus historiques du même
item (incluant les éventuels liens de prêt ancien par entitlement) et les
lignes exactes des `SourceReference` et `OriginLink` v14, **y compris les
références de reçus et les métadonnées de l'Artifact rattaché**, pour détecter
toute modification même lorsque les liens restent présents. La lecture ne
rehache **pas les octets du fichier** ; une éventuelle opération métier devra
contrôler son SHA-256 physique indépendamment. Les lignes historiques sont triées : une variation
d'ordre SQL ou de liste ne fait pas varier le résultat.

Seul **le hash** et des compteurs sont retournés ; aucune référence de licence,
jeton ou valeur brute de reçu n'est projetée. Une valeur non sérialisable
bloque l'empreinte au lieu d'être convertie en texte. L'absence de toute
preuve ne produit pas d'empreinte utilisable.

## Validation locale non autorisante

`validate_paris_adept_local_snapshot(storage, *, loan_id, item_id,
expected_fingerprint)` relit les preuves et compare une empreinte SHA-256
exacte en minuscules :

- `local_evidence_unchanged_remote_attestation_required` : les observations
  actuellement relues correspondent à l'empreinte annoncée.
- `local_evidence_changed` : au moins une preuve enregistrée a changé ;
  reprendre la prévisualisation avant de proposer une autre décision.
- `local_evidence_unavailable` ou `invalid_snapshot_request` : aucune
  validation exploitable.

**Ces trois états ne donnent aucun droit.** Toutes les sorties portent
`return_authorized=false`, `commit_available=false` et
`adobe_profile_resolution=unknown`. L'empreinte est un **contrôle
d'égalité, pas un jeton ni une signature**, et elle ne prouve pas que la
licence Adobe est encore active ou associée au profil observé.

Le hachage n'est **pas atomique** avec les opérations futures : celles-ci
devront obtenir une preuve serveur actuelle et revalider **sous leurs propres
verrous/transactions SQLite**, avec protection TOCTOU, audit et reprise après
incident. Aucune opération destructive ou mutation métier n'est introduite
par cette primitive. L'exposition éventuelle dans la CLI `preview-resolution`
est un incrément distinct après validation des contrats en CI.
