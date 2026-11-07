# P0 #3196 — W1 : identité de profil absente ou non normalisée

La projection Kobo 0.7 ne porte pas de preuve de droit ni de provenance
canonique. Une ligne `backup_items` peut néanmoins contenir un
`identity_id` vide (`""`), blanc ou paddé, tout en ayant un emprunteur,
un statut `complete`, un SHA et un chemin local. Le garde W1 historique
ne refusait que `identity_id IS NULL` : ces libellés pouvaient alimenter
`entitlements`, `acquisition_ledger`, `artifacts`, `placements` et
`catalog_entries` sans identité Core exacte.

Le garde de `Storage.backup_put()` contrôle maintenant **la ligne
fusionnée et persistée dans la même transaction** : si l'identité Kobo
n'est pas une chaîne exacte, non vide et sans blancs périphériques,
la ligne 0.7 reste consultable, mais aucune projection 0.8 positive
n'est produite. Les gardes transactionnels de collision avec les droits
et reçus canoniques déjà présents sont conservés.

Le compte primaire Kobo `account_id=""` est **légitime** et demeure
compatible quand le profil est exact. Les autres fournisseurs ne sont
pas concernés. Aucun fichier, droit fournisseur, OriginLink, reçu de
téléchargement, GET ni archive historique n'est créé ou supprimé par
cette tranche.

Preuve SQLite hors réseau : `tests/test_core_p0_3196_w1_blank_identity_archive.py`.
Le cutover global W1/W2/D1–D4 reste ouvert dans #3196/#4858 ;
ce garde de portée ne constitue pas sa clôture.
