# Core P0 #3196 — Retrait transactionnel des archives Kobo après Sync canonique

Le chemin en production réutilise le **Sync source existant**, son unique
snapshot de droits et son verrou interprocessus d'acquisition. Ce n'est ni une
synchronisation additionnelle, ni une seconde lecture Vault, ni un GET de
contenu, ni une acquisition de droit, ni une copie de fichier.

## Chemin opérationnel

1. `sync_coordination` possède déjà `InterProcessLock(acquisition_lock_file())`.
   Il donne la lease à `canonical_sync_stage.observe_canonical_rights_in_sync`
   pendant les cycles source et opening, **pas** pendant le mode inventory-only.
2. Le stage publie les SourceReferences attestées et requalifie physiquement
   l'état B canonique.
3. `legacy_backup_retirement_sync.retire_canonical_kobo_legacy_in_sync`
   accepte **uniquement** un snapshot provider Kobo complet et courant
   accompagné du vrai verrou encore détenu par le thread appelant. Il
   sélectionne au plus quatre lignes `backup_items.complete` liées
   exactement à l'identity/credential/account et dont la révision correspond
   à la révision positive du fournisseur. Les comptes non qualifiables et
   les autres états restent des archives.
4. Pour chaque ligne, `legacy_backup_retirement.retire_attested_legacy_kobo_backup`
   revérifie **dans `BEGIN IMMEDIATE`** toutes les preuves : SourceRef
   unique et fraîche, révision fournisseur, OriginLink, Placement, reçu de
   livraison terminé, appartenance au profil, SHA/taille et inode, absence
   de collision et contexte de session toujours inchangé. Il écrit le reçu
   append-only `core.legacy-kobo-retirement.v1` dans la même transaction
   que le DELETE **de cette seule ligne legacy**.
5. Le second Sync ne modifie rien ; la copie physique, les droits et toute
   la provenance canonique restent conservés. Le compteur Kobo non relié
   utilise aussi le B canonique exact, voir #4780.

Le budget de quatre retirements borne la durée du verrou et peut reporter les
autres archives à des Sync ultérieurs. Un rejet est conservateur ; la présence
et l'acquisition canonique ne dépendent jamais du succès de la retraite.
Aucune archive inconnue ou incohérente n'est retirée automatiquement.

## Validation et limite de fermeture

`tests/test_core_p0_3196_sync_retirement_gate.py` couvre verrou absent ou
relâché, source partielle, révision absente, non-Kobo, refus SQL, réussite
transactionnelle et idempotence.

**Ce n'est pas la fermeture du ticket #3196.** Les opérateurs W2 de sauvegarde
legacy sans ABI complète et W4 de rapprochement manuel, W5 de relocalisation,
ainsi que certains lecteurs D1–D5, restent à remplacer par des mécanismes
canoniques ou à cantonner strictement aux archives. Aucun purge `DROP`
`backup_items` n'est autorisé.
