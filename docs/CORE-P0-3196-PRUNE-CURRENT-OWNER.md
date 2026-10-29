# Core P0 #3196 — un manifeste v0.7 ne révoque aucun droit

La projection historique `backup_items` utilise seulement la clé `(provider_id, credential_id, item_id)`. Elle ne porte pas une attestation de portée négative complète compatible avec la SourceReference canonique ; elle peut en outre survivre à une réaffectation d'identité ou d'emprunteur.

Le garde originel exigeait une identité, une connexion et un compte exactement liés avant de permettre une désactivation `entitlements`. Ce garde ne suffisait pas : **une concordance de propriétaire ne prouve pas la révocation du droit**. Le Core ne fait donc désormais **aucune mise à jour d'Entitlement depuis `backup_prune_missing`**, y compris quand le propriétaire est connu et le snapshot complet au sens legacy.

Sous `BEGIN IMMEDIATE`, seules les lignes de **projection temporaire** absentes du manifeste sont supprimées. Les origin links, droits, reçus, fichiers et placements restent intacts. Le champ `entitlements_inactivated` continue d'être exposé, toujours à zéro. Les contrôles d'admission du manifeste et l'isolation par provider/credential restent en place.

Une nouvelle autorité négative devra être introduite séparément à partir d'un snapshot fournisseur typé, exhaustif, et validé sous l'owner/compte exact, suivant #3171/#3197. Les tests couvrent les propriétaires exacts, rebonds de credentials, comptes et collisions. La migration 0.7/0.8 et la recette réelle restent ouvertes dans #3196.
