# P0 #3196 — Qualification d'une copie 0.7 déjà publiée en B

## Garantie

Le Core fournit un diagnostic strictement non mutant :

    review_legacy_backup_publication(
        storage, library_root=library_root,
        identity_id=profile, credential_id=kobo_credential,
        item_id=edition,
    )

Ce diagnostic ne transforme pas une sauvegarde historique Kobo en droit
de possession, ne prépare **aucune** migration ni suppression et ne pilote
jamais un GET. Le retour
`status="verified_existing_publication"` signifie seulement que **lors de
cette observation**, l'ancien enregistrement pointe vers un fichier déjà
publié canoniquement et dont le rattachement à la source est démontré.

Pour renvoyer cet état, toutes ces conditions sont nécessaires, sous un
instantané SQLite cohérent :

- enregistrement exact dans `backup_items`, type `complete`, compte et
  révision explicitement enregistrés, SHA et taille valides ;
- aucun autre backup (y compris d'une autre source/identité) ne revendique
  le même `local_path` ;
- connexion Kobo actuelle unique, active, attachée au profil et compte
  emprunteur explicitement enregistré sur **cette** connexion ;
- Placement B `present` du profil exact, `provider_id/source_item_id=NULL`,
  Artifact et taille/SHA correspondants ; un Placeholder legacy ne suffit pas ;
- fichier régulier sous le seul sous-dossier enregistré du profil, sans
  symlink, octets SHA réellement recalculés ; signatures inode du fichier,
  de la racine et du répertoire du profil stables pendant l'inspection ;
- **un seul reçu complet du chemin exact**, dont le stage ID, SourceKey,
  credential, borrower, offre, preuve du fournisseur et OriginLink publié
  sont validés par le gardien Core
  `_completed_path_receipt_evidence` ; absence de toute autre ligne ledger
  nommant le même chemin, même dans des métadonnées imbriquées.

Le champ historique `revision_id` est exigé ici à titre d'inventaire, mais
**n'est pas relié à un champ de révision dans le SourceKey canonique** :
ce diagnostic n'atteste donc **pas** l'égalité de versions. Une future
migration devra résoudre ce contrat avant un changement de référence.
Les auteurs, titres, ISBN et SHA seuls ne sont jamais une preuve d'origine.

## Frontières

- Résultats fermés `blocked` ou
  `verified_existing_publication` ; raisons fermées, sans compte,
  chemin, numéro d'achat ni reçu divulgué.
- `read_only=True` et `may_migrate_or_retire=False` dans **tous** les
  cas. Aucun plan-token ni autorisation réutilisable. Aucune écriture SQLite,
  accès fournisseur, ADEPT ou opération sur les octets en dehors du rehash.
- Le déplacement, l'import d'un vieux backup sans reçu B, les migrations
  SQL, l'archivage du writer, la permission de téléchargement ou la
  déduplication restent des processus distincts.
- Après toute modification de source, compte, métadonnée, fichier ou montage,
  cette observation devient obsolète. Une future transaction de migration
  devra **tout revérifier sous verrou** et fournir rollback/reprise.
- Le gel des nettoyages destructifs (#4496) reste applicable.

## Tests

Tests purement synthétiques v14 : publication positive, absence de
backup/révision/fichier/reçu/OriginLink, compte étranger ou révoqué,
changement de SHA/taille, mauvais profil, symlink, collision de chemin
entre sources et de reçus entre comptes, déclaration de profil ambiguë
et JSON de reçu malformé. Les tests vérifient qu'aucune mutation de fichiers
ou de références de provenance n'est réalisée par le diagnostic.

## Pour clore #3196

Il faut encore qualifier les backups historiquement **non** publiés en B,
livrer le nouveau contrat révision/version et les preuves de reprise, migrer
en transaction idempotente sous verrou et remplacer tous les consumers
décisionnels, puis désactiver les writers legacy sans perte des journaux
ADEPT et backups. L'inspection positive n'accomplit aucune de ces étapes
par elle-même.
