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
| W3 | `Storage.backup_prune_missing()` appelée par `_prune_successful_manifest` | `DELETE` des **lignes de projection** 0.7 absentes d'un manifeste admis ; **jamais** de révocation d'Entitlement/OriginLink canonique ni suppression de fichier | Après manifeste complet du credential exact. Les clés brutes inadmissibles sont préservées depuis #4548. **Une ligne Kobo avec `local_path` non vide est désormais également préservée** : la disparition du manifeste ne prouve ni migration ni absence des octets et ne doit pas effacer le dernier pointeur 0.7 d'un livre éventuellement hors ligne. Les lignes sans pointeur restent prunables **sauf lorsqu'elles sont explicitement en `acquiring` / `pending_acquisition` (reprise interrompue) ou `reconcile_required` / `reconcile_conflict` (rapprochement manuel)** : ces quarantaines de rapprochement ne peuvent être effacées par un inventaire distant qui omet l'édition. Le rapprochement manuel exact, pas le manifeste, décidera de leur sort. **Lors de la reprise Kobo W2/W3, un conflit entre plusieurs fichiers candidats conserve désormais le `local_path` historique d'origine au lieu de le réinitialiser à `NULL`** : il reste purement indicatif et l'état `reconcile_conflict` bloque toute réutilisation/GET sans preuve. Cela empêche une session suivante de supprimer cette ligne au prune du manifeste. Sans pointeur initial, aucun chemin n'est arbitrairement choisi. Le compteur `local_preserved` historique ne compte que les fichiers qui auraient été laissés après suppression de leur projection ; les lignes retenues ne sont pas comptées comme `pruned`. |
| W4 | `core/kobo_local_reconciliation.py::reconcile_kobo_local` | Écrit sous confirmation explicite `backup_items`, acquisition, entitlement, Artifact et Placement via `_write_row` ; undo journalisé | Rapprochement manuel legacy avec compte/chemin/SHA exact. **Refuse désormais toute SourceReference canonique sur le même credential/édition, même autre emprunteur, ainsi que tout checkpoint `canonical.completed-delivery.v1`** : seuls les dossiers réellement legacy restent accessibles. Doit être remplacé avant coupure. |
| W5 | `Storage.relocate_local_path`, `Storage.commit_directory_migration` et relocalisations historiques | La relocalisation ordinaire refuse de réécrire `backup_items.local_path` pour une édition Kobo déjà typée. **La migration de répertoire refuse désormais de fusionner deux Placements au même chemin cible si propriétaire, Artifact, état ou portée historique/canonique divergent** : seule la déduplication de deux Placements `present` locaux sans fournisseur, même SHA Artifact et aucune référence `backup_items` / `origin_links`, est conservée. Les vrais déplacements journalisés de répertoire (sans collision de Placements) restent possibles. | Les migrations de chemins ne prouvent pas les droits : les relinks canoniques disposent d'un opérateur distinct avec OriginLink/SourceKey/compte attestés. W5 n'est pas archivé globalement et les cas de collision avec preuve devront disposer d'une migration spécialisée. |

**Autre entrée W5 retirée :** `LibraryService._migrate_legacy_flat_kobo_files`
conserve sa signature privée de façade mais lève désormais immédiatement
`legacy_kobo_flat_migration_retired`, sans ouvrir SQLite, copier ou détruire
un fichier. L'ancienne implémentation copiait puis effaçait un fichier
sur l'autorité insuffisante d'une ligne `backup_items`. Aucun appel de
production n'était identifié ; le veto explicite permet de détecter un
éventuel ancien appel au lieu d'effectuer un déplacement non attesté.
La migration journalisée des répertoires d'identités et les relinks canoniques
attestés restent des opérations distinctes, non retirées par cette tranche.

**Gel de surface :** le test
`tests/test_core_p0_3196_residual_writer_surface.py` détecte les nouveaux
appels Python directs `.backup_put()` / `.backup_prune_missing()` hors de
`core/kobo_backup.py`. Il ne prétend **pas** détecter toute écriture SQL
dynamique (W4–W5) ; une revue de code de celles-ci reste indispensable.

## 2. Lecteurs qui influencent encore une décision

| ID | Point de lecture | Effet restant / dépendance |
| --- | --- | --- |
| D1 | `core/kobo_backup.py` — `backup_get/list`, `_skip_manifest_item`, `_recover_local_remnants`, `kobo_backup_preview` | **L’aperçu Kobo 0.7 ne classe plus une édition avec `SourceReference` typée (credential/édition exacts, même autre profil/emprunteur) comme `already_local_linked`, `already_local_verified`, `candidate_unverified` ou `potentially_missing`** : elle relève de `attention_required` (sauf archivage **explicitement fourni par un manifeste rafraîchi** : `skipped_archived`), sans attester B ni autoriser un GET. Les éditions purement historiques conservent la compatibilité. Les autres décisions internes `unchanged`, `reconcile_required`, skip et admission dépendent encore du 0.7 : le garde de chemin #4546 vérifie l'owner mais **ne prouve pas un OriginLink canonique**. |
| D2 | `core/source_materialization.py` — `_evidence()`, `completed()` | Aperçu/confirmation legacy, empreintes d'historique et décompte post-exécution. Bloqué si ABI Core complète **ou si le manifeste contient une édition ayant déjà une `SourceReference` Kobo pour le même credential, même autre borrower/profil** : la préparation refuse toute confirmation 0.7 et l'exécution revérifie après token. Sans référence typée, cette voie dépend encore de la table historique. |
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
