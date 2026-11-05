# P0 #3196 — Points résiduels 0.7 et contrat de retrait (05/11/2026)

> **Statut : inventaire de code, pas migration.** Analyse de `main` après les
> fusions #4539, #4545, #4546, #4548, #4557 et #4562. Le succès des tests
> synthétiques ne prouve ni l'état d'un compte Kobo réel ni l'existence de
> fichiers. Ne pas prendre ce document comme autorisation de `DROP`, de GET,
> de copie de livres, de conversion de droits ou de désactivation du dernier
> chemin de reprise.

## 1. Écritures actives (liste finie)

| ID | Code / entrée | Mutation 0.7 restante | Pourquoi elle est encore atteignable |
| --- | --- | --- | --- |
| W1 | `Storage.backup_put()` → `Storage._mirror_backup_row()`, `storage.py` | UPSERT `backup_items` **et miroir transactionnel** `entitlements`, `acquisition_ledger`, parfois `artifacts`, `placements`, `catalog_entries` (ces trois derniers ne constituent **pas** une preuve physique B) | Compatibilité des comptes historiques **sans référence canonique pour l'édition** : si une `SourceReference` Kobo typée existe déjà sur le credential/édition (quel que soit son compte), le writer ne met à jour **que** la projection `backup_items`, après les contrôles de collision ; ne synthétise alors plus droit, journal ni placement. Un reçu Core `completed-delivery` exact reste aussi protégé. Le writer demeure actif pour le legacy pur. |
| W2 | `core/kobo_backup.py` — `_refresh_manifest_record`, `_skip_manifest_item`, `_recover_local_remnants` et boucle de livraison 0.7 | Appels directs à `storage.backup_put` (manifest, archivage, reprise, réparation requise, réception/erreur) **uniquement pour les éditions encore sans SourceReference canonique** : les références typées sur le credential/édition interdisent la reprise, les mutations du manifest et le GET historique, même avec ABI partielle ; compte/identité alternatifs sont un veto conservateur. | `LibraryService.sync_kobo_backup`, synchronisation ciblée et reprise legacy restent exécutables pour le legacy pur sans référence typée. **Une session avec ABI v1/v2 complète est bloquée**. L'interruption historique demeure conservée, sans conversion en droit ni preuve de placement. |
| W3 | `Storage.backup_prune_missing()` appelée par `_prune_successful_manifest` | `DELETE` des **lignes de projection** 0.7 absentes d'un manifeste admis ; **jamais** de révocation d'Entitlement/OriginLink canonique ni suppression de fichier | Après manifeste complet du credential exact. Les clés brutes inadmissibles sont préservées depuis #4548. |
| W4 | `core/kobo_local_reconciliation.py::reconcile_kobo_local` | Écrit sous confirmation explicite `backup_items`, acquisition, entitlement, Artifact et Placement via `_write_row` ; undo journalisé | Rapprochement manuel legacy avec compte/chemin/SHA exact. **Refuse désormais toute SourceReference canonique sur le même credential/édition, même autre emprunteur, ainsi que tout checkpoint `canonical.completed-delivery.v1`** : seuls les dossiers réellement legacy restent accessibles. Doit être remplacé avant coupure. |
| W5 | `Storage.relocate_local_path` et relocalisations historiques du stockage (notamment lors de migration de dossier d'identité) | La relocalisation ordinaire **refuse désormais** de réécrire `backup_items.local_path` lorsque le credential/édition Kobo possède une `SourceReference` (même autre emprunteur/profil), avant toute mutation de placement ; les dossiers strictement 0.7 restent compatibles. Les autres parcours de migration et le relink canonique attesté restent à qualifier. | La migration de chemins est distincte de la livraison : l'opérateur Core de déduplication avec preuves OriginLink exactes peut effectuer un relink sous contrôle, **pas** une simple normalisation sur reçu legacy. W5 n'est pas encore archivé globalement. |

**Autre méthode existante :** `LibraryService._migrate_legacy_flat_kobo_files`
contient des copies de fichiers + `relocate_local_path` fondées sur d'anciennes
déclarations. Aucun appel depuis les sources de production courantes n'a été
identifié par la recherche du symbole. Ne **pas** l'exécuter pour démontrer sa
disparition ; décider explicitement de sa suppression ou de sa mise en lecture
seule lors du cutover.

**Gel de surface :** le test
`tests/test_core_p0_3196_residual_writer_surface.py` détecte les nouveaux
appels Python directs `.backup_put()` / `.backup_prune_missing()` hors de
`core/kobo_backup.py`. Il ne prétend **pas** détecter toute écriture SQL
dynamique (W4–W5) ; une revue de code de celles-ci reste indispensable.

## 2. Lecteurs qui influencent encore une décision

| ID | Point de lecture | Effet restant / dépendance |
| --- | --- | --- |
| D1 | `core/kobo_backup.py` — `backup_get/list`, `_skip_manifest_item`, `_recover_local_remnants`, `kobo_backup_preview` | 0.7 `complete`/chemin/révision et candidats locaux déterminent `unchanged`, `reconcile_required`, skip et admission à la livraison. Le garde de chemin #4546 vérifie l'owner ; il **ne prouve pas un OriginLink canonique**. |
| D2 | `core/source_materialization.py` — `_evidence()`, `completed()` | Aperçu/confirmation legacy, empreintes d'historique et décompte post-exécution. Bloqué si ABI Core complète ; sinon dépend encore de la table. |
| D3 | `services.py::_acquire_direct_item_impl` | `backup_get` et chemin local contribuent au veto de téléchargement manuel Kobo historique. **Une ABI complète ou une `SourceReference` typée sur le credential/édition (même autre borrower/identité) désactive désormais ce GET 0.7**, avec relecture sous verrou après admission ; l'UI ne propose plus cette action sur cette édition. Le legacy sans référence typée reste actif. |
| D4 | `core/source_materialization_schedule.py`, `core/sync_coordination.py`, façade `services.py` | Les préférences exact-source et le checkpoint pilotent toujours la **planification** de W2 en l'absence d'ABI complète. L'ancien booléen global n'autorise pas un GET. |
| D5 | `core/kobo_local_reconciliation.py` | Préflight, transaction et undo du mécanisme W4 requièrent l'ancienne ligne : **non retirable par simple renommage**. |
| D6 | `core/kobo_local_audit.py`, `kobo_backup_diagnostic`, `kobo_backup_preview`, `kobo_backup_records`, `core/backup_projection_audit.py` et `core/legacy_backup_migration_inventory.py` | Consultation/inventaire/admission à revue **sans preuve d'origine B**. Ces lecteurs peuvent rester pour l'archive, à condition de rester strictement passifs. |

**Lectures négatives à préserver :** `canonical_promotion`,
`canonical_dedup_*`, `duplicate_maintenance_guard`,
`missing_placement_*`, `library_local_inspection` et
`library_audit_stability` consultent la présence d'une référence historique
afin de **refuser** une suppression ou une relocalisation non sûre. Les retirer
sans garde de remplacement créerait une perte de référence, même si aucune de
ces lectures n'autorise de droit ou de présence locale.

Le pipeline canonique `canonical_selected_delivery` →
`canonical_publication` / `convergence_execution` ne possède pas de
`backup_put` : une offre et un fichier physiquement attestés empruntent déjà
le chemin B sans projeter une acquisition dans `backup_items`.

## 3. Séquence de migration et barrières de sortie

1. **Classer, ne pas effacer.** Par identité / credential / compte / édition /
   révision exacts, utiliser les diagnostics existants
   `inventory_legacy_kobo_backups` et
   `review_legacy_backup_publication`. Inclure les clés non qualifiables,
   la provenance vide, les interruptions, les chemins hors scope et les
   collusions interprofils. Ne pas appliquer une migration à un rapport
   `budget_exceeded` / `scope_unverified`.
2. **Gérer séparément le cas déjà publié en B.** Une seule combinaison
   révision / SourceKey / borrower / reçu terminé / OriginLink / Placement
   / SHA-inode du bon profil peut être reconnue comme doublon historique.
   L'inspection #4510 ne vérifie pas encore l'égalité de **révision** : elle
   ne donne donc **pas** une permission de migration exécutable.
3. **Gérer le cas non encore publié en B.** Ni `backup_items.complete`,
   ni `sha256`, ni le titre, ni l'ISBN ne créent un droit, un fichier B ou
   un `CompletedDelivery`. Obtenir une offre actuelle + consentement +
   livraison canonique, ou maintenir un dossier `reconcile_required`
   pour intervention explicite. Préserver les états `acquiring`,
   `pending_acquisition`, les checkpoints ADEPT et les sources sans v2 :
   ils ne doivent pas devenir des doublons téléchargés à l'aveugle.
4. **Effectuer la migration transactionnelle/reprise.** Sous verrou
   d'acquisition partagé + `BEGIN IMMEDIATE`, préparer une empreinte
   bornée, revérifier account/revision/source, les collisions de chemins et
   les octets, committer de façon idempotente et pouvoir reprendre un crash.
   Les octets et la provenance restent distincts par identité.
5. **Remplacer W2/D1–D4 puis W4.** Une entrée sans ABI v2 et toujours
   opérationnelle **bloque la désactivation globale**. Passer chaque
   méthode de livraison (manuel, startup, confirmation, retry) au moteur
   canonique ; basculer l'undo/reconcile vers des références B exactes.
   W5 doit alors être archivé ou réécrit en préservant les historiques.
6. **Preuve de coupure.** Aucun writer 0.7 accessible, aucune
   décision `unchanged` / `local_present` / veto fournisseur sur la
   seule ligne `backup_items`; rejouabilité/crash testés sur comptes A/B,
   SHA identiques, révisions divergentes, symlinks et acquis disparus ;
   diagnostics d'archives encore lisibles, droits et OriginLinks inchangés.
   **Ne pas supprimer la table historique** pour réussir ce critère.

### Critère de fermeture de #3196

Fusion et CI/installation des migrations/cutovers, puis preuve par tests que
**W1–W5 ne mutent plus les acquisitions courantes** et **D1–D5 ne donnent
plus d'autorité positive à `backup_items`** ; aucune perte de reprise sur
source legacy ni de provenance canonique. Les méthodes de lecture d'archives
et les gardes négatifs peuvent persister sans empêcher la fermeture.

**État du 05/11 : NON atteint.** Les protections #4539–#4562 ne sont pas
équivalentes à une coupure du writer.
