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
| W1 | `Storage.backup_put()` → `Storage._mirror_backup_row()` | **Coupure ciblée des reçus Kobo sans identité ou emprunteur lié** : la ligne `backup_items` subsiste, mais aucune écriture canonique n'est synthétisée tant que le tuple persistant `identity_id/account_id` est incomplet. Les contrôles de propriétaire canonique restent en lecture seule. Une ligne déjà liée ne perd pas son owner sur un patch partiel. Le miroir est **encore actif** pour les comptes Kobo liés, et pour les autres fournisseurs. | La tentative de couper W1 d'un coup a provoqué **124 échecs et 11 erreurs** dans la CI initiale (rapprochement manuel W4, bibliothèque et reprise), car ces composants utilisent encore le miroir historique. La PR est volontairement réduite : une coupure globale n'est possible qu'avec migration/reprise et remplacement W2/D1–D5. Le format des archives 0.7 reste consultable et le rollback de l'ancien schéma inchangé. |
| W2 | `core/kobo_backup.py` — `_refresh_manifest_record`, `_skip_manifest_item`, `_recover_local_remnants` et boucle de livraison 0.7 | Appels directs à `storage.backup_put` (manifest, archivage, reprise, réparation requise, réception/erreur) **uniquement pour les éditions encore sans SourceReference canonique** : les références typées sur le credential/édition interdisent la reprise, les mutations du manifest et le GET historique, même avec ABI partielle ; compte/identité alternatifs sont un veto conservateur. | `LibraryService.sync_kobo_backup`, synchronisation ciblée et reprise legacy restent exécutables pour le legacy pur sans référence typée. **Une session avec ABI v1/v2 complète est bloquée**. L'interruption historique demeure conservée, sans conversion en droit ni preuve de placement. **W2 durci** : une tentative `acquiring` sans fichier reconnue au scan devient `pending_acquisition` + code fermé `interrupted_acquisition_review` et **ne déclenche plus de GET** lorsque le même item est encore annoncé au manifeste ; le libellé historique de l'interruption conserve également ce veto. Un `pending_acquisition` simple issu d'un adaptateur indisponible avant toute tentative n'est pas bloqué. La révision historique n'est pas remplacée par celle du manifeste avant réconciliation. Reprise explicite, qualification des octets éventuels et migration canonique restent à implémenter. |
| W3 | `Storage.backup_prune_missing` / ancien `_prune_successful_manifest` | **RETIRÉ pour Kobo : zéro suppression, même sur manifeste complet, et API de compatibilité sans accès SQL**. Les autres fournisseurs gardent leur prune de projection. | Aucune entrée Kobo atteignable : scheduler retiré, compatibilité retourne zéro sans transaction. Toutes les lignes v0.7 sont archivées jusqu'à migration explicite (même `pending` sans indices). Tests `tests/test_core_p0_3196_w3_prune_retired.py` et contrats W3 existants. |
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

**Préparation de la migration exacte (06/11/2026) :** l'opérateur exécutable `core/legacy_backup_retirement.py::retire_attested_legacy_kobo_backup` peut retirer **une seule** ligne `backup_items.complete` Kobo déjà entièrement supersédée. Il exige la concordance `identity/credential/account/branch/edition/right/revision` avec une **nouvelle** observation fournisseur réussie, exhaustive et horodatée (fraîcheur <= 15 min), la source canonique `provider_response` correspondante, un seul propriétaire du credential/édition, un OriginLink canonique, un Placement présent, un reçu `canonical.completed-delivery.v1` exact sans collision, et des octets SHA/taille/inode vérifiés dans le profil. Contrôle `context_current` avant/après le commit, `BEGIN IMMEDIATE`, journal versionné append-only contenant l'archive complète avant `DELETE` atomique ; aucun GET, déplacement ou révocation. Répéter est sans effet. Cet opérateur n'est **pas encore branché au Sync** : W2/D1 restent des dépendances à migrer, le journal ne crée aucun droit. Tests positifs, négatifs et rollback dans `tests/test_core_p0_3196_exact_legacy_retirement.py`. Tout usage en production nécessite le verrou d'acquisition partagé et la vue fournisseur authentifiée du compte courant. 

**Mise à jour W1/W2 — raccordement au Sync canonique (06/11/2026) :**
`execute_admitted_convergence` appelle maintenant l'opérateur
`retire_attested_legacy_kobo_backup` **seulement après** vérification
indépendante d'un B déjà présent (OriginLink + Placement) ou publication
complète A→B. L'appel s'effectue sous le verrou unique d'acquisition détenu
par Sync. Un seul inventaire SQL borné (10 000 clés maximum) sélectionne les
archives `backup_items.complete` Kobo de l'identité/compte exacts ;
l'opérateur recontrôle ensuite sous `BEGIN IMMEDIATE` la source, la révision
fraîche issue d'un snapshot fournisseur complet et réussi, le reçu autonome,
l'unicité du propriétaire et les octets SHA/taille/inode avant archivage
journalisé + suppression de la **seule** ligne redondante. La moindre preuve
manquante laisse la ligne 0.7 intacte. Les erreurs et refus de ce nettoyage
post-B ne déclassent pas le résultat Sync et ne déclenchent jamais de GET.
Les statuts `reconcile_required`, `acquiring`, `pending` et les autres
fournisseurs ne sont pas candidats. La coupure W4 et la migration des dossiers
historiques sans B indépendant restent ouvertes. Recette SQLite + fichier
dans `tests/test_core_p0_3196_sync_retirement_integration.py`.

**Mise à jour W1 (06/11/2026) :** le checkpoint Kobo `skipped_archived` ne déclenche plus `_mirror_backup_row` lors de `backup_put`, même avec `identity_id/account_id` renseignés. L'archive reste dans `backup_items`, avec contrôle transactionnel de collision d'owner. Aucun nouveau droit, ledger, Artifact, Placement ou catalogue ne peut être créé/rafraîchi par cet état d'archivage. **Les statuts `reconcile_required` et `reconcile_conflict` conservent temporairement le miroir W1** : la première CI a révélé 46 échecs et 11 erreurs sur W4/confirmation et leurs consommateurs lorsqu'il était supprimé sans remplacement. Ils doivent être migrés avec W4, pas coupés isolément. Les autres états Kobo et fournisseurs restent compatibles. Aucune ancienne projection n'est supprimée. Test `tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py`.

**Mise à jour W3 (06/11/2026) :** l'intégralité du prune Kobo est retirée, y compris pour les enregistrements `pending` sans aucune preuve ; les détails historiques de garde W3 ci-dessous décrivent les protections antérieures désormais subsumées par la conservation de *toutes* les lignes. La migration transactionnelle seule pourra décider du retrait d'une archive.

**Complément W2/W3 — orphelins avec indices d'octets :** un ancien enregistrement Kobo sans `local_path` mais avec `sha256` non vide et/ou `size` positive reste consultable après un manifeste sans l'édition. Ce sont des indices historiques, pas des fichiers attestés ni des droits. À la réapparition de l'édition, W2 reconnaît aussi `error`/`pending` portant ces indices, **fige la révision acquise**, conserve SHA/taille et impose `reconcile_required` sans GET automatique. Ce garde de conservation/quarantaine n'efface rien et ne réalise pas le cutover ; tests SQLite et service sans fournisseur réel dans `tests/test_core_p0_3196_w3_orphaned_byte_evidence.py`.

**Complément W3 — refus de délégation :** `backup_prune_missing` conserve désormais un refus `attention_required` avec `attention_code=missing_delegation_scope`, même sans chemin local lorsque l'édition sort temporairement d'un manifeste complet. La disparition de l'édition ne démontre pas un nouveau scope ; le retour du manifeste ne doit jamais réarmer implicitement le GET refusé. Seule une reprise explicite du compte ou un cutover canonique attesté peut lever ce checkpoint ; test SQLite et synchronisation sans réseau dans `tests/test_core_p0_3196_w3_scope_denial_retention.py`.

**Garde W4 sur le chemin cible :** si le Placement existe et que son
Artifact a un OriginLink pour la même identité, le rapprochement 0.7 refuse
la prévisualisation, la confirmation et l'annulation. Une autre édition ou
connexion peut s'appuyer sur ce Placement, même quand l'édition Kobo legacy
n'a aucune SourceReference. Une provenance d'une autre identité ne suffit
pas à bloquer une copie indépendante. Les écritures W4 purement legacy
restent accessibles.

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
| D3 | `services.py::_acquire_direct_item_impl` | `backup_get` et chemin local contribuent au veto de téléchargement manuel Kobo historique. **Une ABI complète ou une `SourceReference` typée sur le credential/édition (même autre borrower/identité) désactive désormais ce GET 0.7**, avec relecture sous verrou après admission ; l'UI ne propose plus cette action sur cette édition. Le legacy sans référence typée reste actif : **en l'absence d'une admission Core fraîche**, le D3 sous verrou refuse tout pointeur 0.7 `local_path` (même disparu), SHA/taille, ancienne acquisition `complete/acquiring/reconcile*`, interruption documentée, archive 409 ou refus de délégation 403 avant un autre GET. Cela ne prouve ni droit, ni présence B ; `pending` jamais commencé et sans indice reste admissible à l'action explicite. Une **admission Core actuelle**, déjà qualifiée par `owned_library_download`, conserve priorité : un vieux statut ou chemin 0.7 ne doit jamais opposer un veto à un droit fournisseur indépendamment attesté. Les tests couvrent les deux branches. Voir `tests/test_core_p0_3196_d3_legacy_recovery_checkpoint.py`. |
| D4 | `core/source_materialization_schedule.py`, `core/sync_coordination.py`, façade `services.py` | Les préférences exact-source et le checkpoint pilotent toujours la **planification** de W2 en l'absence d'ABI complète. L'ancien booléen global n'autorise pas un GET. |
| D5 | `core/kobo_local_reconciliation.py` | Préflight, transaction et undo du mécanisme W4 requièrent l'ancienne ligne : **non retirable par simple renommage**. |
| D6 | `core/kobo_local_audit.py`, `kobo_backup_diagnostic`, `kobo_backup_preview`, `kobo_backup_records`, `core/backup_projection_audit.py` et `core/legacy_backup_migration_inventory.py` | Consultation/inventaire/admission à revue **sans preuve d'origine B**. Ces lecteurs peuvent rester pour l'archive, à condition de rester strictement passifs. |
| D7 | `core/library_projection.py::local_placements_for_sources` (recherche publique) **et** `local_placement_for_source` (cible individuelle) | **Cutover lecture typée :** si une `SourceReference` Kobo existe pour `credential/edition`, aucun des deux lecteurs n'infère un fichier local depuis un vieux `acquisition_ledger.complete`. L'alias public sans compte exige une SourceReference unique du profil, un OriginLink fournisseur et un Placement ouvrable sous l'identité ; toute deuxième source/droit/emprunteur bloque l'alias. Le lecteur à compte exact sélectionne la seule SourceReference du bon borrower, son OriginLink et le même Artifact, puis conserve sa vérification physique SHA/taille du fichier. Si l'OriginLink manque, les octets sont absents ou le scope est ambigu : aucun match. Les éditions non typées continuent la projection legacy en lecture seule, sans création de droit. |

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
   ne donne donc **pas** une permission de migration exécutable. L'extension
   descriptive optionnelle `provider_revision_id` aux observations Core v1/v2
   conserve désormais une révision fournisseur exacte à travers l'adaptateur,
   sans fabriquer une attestation : il faut que **le plugin d'un fournisseur
   authentifié la fournisse**, puis la comparer au reçu historique sous le
   verrou de migration, avec toutes les autres preuves B. Champ absent =
   comparaison impossible ; champ présent ≠ permission de mutation.
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

**État du 06/11 : NON atteint.** **W1 bloque désormais le miroir canonique pour les reçus non liés, et W3 est retiré** : aucun manifeste ne supprime désormais une archive Kobo. W1 ne concerne encore que les éditions **déjà typées** ; les entrées 0.7 sans SourceReference n'ont pas été migrées et les writers W2, W4 et W5 restent accessibles dans leur périmètre. Les protections #4539–#4562 ne sont pas
équivalentes à une coupure du writer.
