# Core P0 #3196 — protéger les entitlements canoniques du replay v0.7

`Storage.backup_put()` conserve le writer historique Kobo et un miroir vers le ledger générique pour la compatibilité. Avant ce lot, `_mirror_backup_row()` faisait un UPSERT destructif d'un `entitlement` existant pour le même identifiant stable : un ancien état `complete/pending` pouvait réactiver un droit `inactive`, écraser la révision, l'expiration et surtout les métadonnées de provenance attestées par le provider.

## Contrat

Sous **la transaction SQL déjà détenue**, le writer inspecte l'entitlement d'identité stable et contrôle le tuple provider/credential/account/item/type/loan/identity. Toute collision de propriétaire échoue atomiquement comme auparavant.

Si le droit existant n'est **pas** identifié comme projection `compatibility_projection=backup_items` ou `imported_from=backup_items`, le miroir le **laisse inchangé** : état, source, preuve, dates et révision. Il peut néanmoins conserver l'ancien `backup_items` et son acquisition historique, qui ne deviennent pas une nouvelle autorité de droit.

Si le droit est encore purement hérité, les mises à jour existantes restent disponibles pendant la migration. Aucune preuve de droit n'est inférée depuis un titre, un SHA ou un statut de téléchargement.

## Vérification

Les tests SQLite `tests/test_core_p1_3196_backup_owner_upsert.py` exercent la priorité du droit canonique actif ou inactif sur plusieurs replays et la promotion ultérieure d'une projection legacy en droit attesté. Les suites existantes couvrent les collisions de propriétaire et les transitions historiques.

Limites : les autres writers/readers hérités, le modèle de migration 0.7/0.8, les obligations de prêts live et la reprise crash restent à qualifier avant fermeture de #3196. Aucun P2.
