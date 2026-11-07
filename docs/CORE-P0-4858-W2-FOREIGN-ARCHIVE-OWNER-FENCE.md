# P0 #4858 — W2 : interdiction de reprise inter-emprunteurs d'une archive Kobo

## Défaillance identifiée

Le schéma historique `backup_items` v0.7 est indexé par
`(provider_id, credential_id, item_id)` : **ni identité Core, ni compte
emprunteur**. Dans `_refresh_manifest_record`, une session Kobo active
lisait une archive existante de cette clé, remplaçait sans condition
`identity_id/account_id` par ceux de la nouvelle session et **conservait**
`local_path/sha256/size/status/revision_id` et les preuves d'acquisition
précédentes. Une connexion réaffectée pouvait ainsi attribuer à B
l'acquisition ou les octets historiques de A, et le fast-path de reprise
pouvait éviter à tort une acquisition réelle.

## Coupure bornée

Avant de lire `cover_url`, de modifier le checkpoint ou de décider une
acquisition, une archive portant une **identité explicite non vide**
ou un **emprunteur explicite** différent de la source courante lève
`kobo_legacy_archive_owner_changed` et abandonne la source par la voie
`_KoboLegacySessionChanged` existante. L'archive reste intacte, sans
migration ni suppression. Un même credential avec deux borrowers n'est
pas une preuve de continuité.

**Compatibilité 0.7 nécessaire :** les archives sans propriétaire
(`identity_id` ou `account_id` absent) continuent leur ancienne
reprise bornée ; elles ne sont **pas** promues en provenance typée A/B
et nécessitent une migration attestée distincte. La première variante
refusant aussi les propriétaires absents provoquait 29 échecs de
régression Core (réconciliation locale et reprises sans téléchargement) ;
la coupure a donc été volontairement limitée aux **contradictions
explicites**, sans inventer une attribution aux archives inconnues.

Le compte primaire Kobo `account_id=""` demeure valide **si la
valeur persistée est exactement vide** et l'identité exacte. Le
mécanisme ne modifie pas les autres fournisseurs.

## Tests et sortie

`tests/test_core_p0_4858_w2_foreign_archive_owner_fence.py` :
quatre portées explicitement contradictoires, aucune écriture ni appel fournisseur,
continuité du compte exact et du primaire vide, archive SQLite inchangée.

Ce garde négatif **ne constitue pas un cutover W2 complet**. La sortie
définitive exige l'ABI A→B canonique, une preuve fournisseur actuelle
et une migration/reprise explicite des archives historiques, sans
GET redondant ni transfert implicite de droits.
