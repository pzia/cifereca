# Core P0 #3196 — un manifeste courant ne révoque pas l'ancien propriétaire

Le `backup_items` v0.7 possède une clé seulement `(provider_id, credential_id, item_id)` : une connexion réaffectée à une autre identité ou à un autre compte peut donc contenir des lignes anciennes d'un emprunteur précédent. Dans ce contexte, un manifeste frais du **nouveau** compte attestant l'absence de l'item ne peut pas désactiver l'Entitlement de l'ancien compte.

Sous la transaction SQL `BEGIN IMMEDIATE` déjà détenue par `Storage.backup_prune_missing`, le Core vérifie désormais avant **toute inactivation de droit** :

- l'Entitlement candidat porte les mêmes coordonnées exactes que la projection (identité, fournisseur, credential, account, item, type, loan) ;
- la connexion du couple provider/credential existe **une fois** et est activée sur **cette** identité ;
- le compte `provider_accounts` du même couple et de l'identité enregistrée existe **une fois** avec le même account ID ;
- le droit est encore une projection explicitement marquée `backup_items`.

Sans ce triplet actuel, le Core peut purger l'ancienne ligne **de compatibilité** (comportement préexistant), mais **ne change pas** l'Entitlement ni son historique d'acquisition. Il n'infère jamais l'absence de prêt ni d'achat du fait d'une connexion réaffectée. Aucune mutation des Artifacts, Placements ou OriginLinks.

## Régressions

`tests/test_core_p0_3196_prune_exact_right_scope.py` couvre la connexion originale exacte, la réaffectation à une autre identité, la réaffectation à un autre compte et l'absence d'enregistrement courant. Les gardes de manifeste malformé, de collision et de portée du droit restent dans la même suite.

## Limitations

Le modèle de projection historique reste temporairement présent et son prune en tant que ligne de compatibilité demeure autorisé. Il ne faut pas en déduire un retour fournisseur, une preuve d'origine ou une récupération de prêt. Les migrations complètes et recettes live demeurent nécessaires à la fermeture #3196. Aucun P2.
