# P0 #3196 — Points résiduels 0.7 et contrat de retrait (05/11/2026)

> **Statut : inventaire de code, pas migration.** Analyse de `main` après les
> fusions #4539, #4545, #4546, #4548, #4557 et #4562. Le succès des tests
> synthétiques ne prouve ni l'état d'un compte Kobo réel ni l'existence de
> fichiers. Ne pas prendre ce document comme autorisation de `DROP`, de GET,
> de copie de livres, de conversion de droits ou de désactivation du dernier
> chemin de reprise.

> **Cutover post-revue 07/11/2026 : #4928 (retrait physique W4) et #4929 (W1 sans source active) FUSIONNÉES.**
> W4 n'a plus d'exécuteur positif dans le Core livré ; les archives W4 et W1
> restent consultables. Le W1 des réconciliations sans source Kobo active
> n'alimente plus le modèle 0.8, même avec SHA/taille historiques complets.
> W1 sur source active, W2 ABI partielle et D1–D4 internes restent ouverts.
>
> **Mise à jour vérifiée le 07/11/2026 (PR #4922 et #4924 fusionnées).**
> L'entrée Python publique `reconcile_kobo_local` refuse désormais toutes les
> variantes legacy avant SQLite/FS ; les façades CLI, CoreUI et offline étaient
> déjà retirées. L'exécuteur historique W4 a désormais été **extrait
> physiquement du module de production** vers `tests/kobo_legacy_reconciliation_archive.py`.
> Le Core livré ne contient plus cet exécuteur positif ; cela ne publie
> aucune preuve A→B et ne ferme ni W1 ni W2. La commande CLI
> `kobo-resume-preview` **sans** `--refresh-manifest` lit désormais les preuves
> canoniques B existantes dans SQLite, sans session Kobo ni lecture de
> `backup_items` ; les appelants internes qui utilisent l'ancien aperçu
> n'ont pas tous migré. **W1 et W2 restent ouverts**, avec les lecteurs D1–D4
> internes, et la fermeture de #3196 exige toujours la recette intégrée.

## 1. Écritures actives (liste finie)

| ID | Code / entrée | Mutation 0.7 restante | Pourquoi elle est encore atteignable |
| --- | --- | --- | --- |
| W1 | `Storage.backup_put()` → `Storage._mirror_backup_row()` | **Coupure ciblée des reçus Kobo sans identité ou emprunteur lié** : la ligne `backup_items` subsiste, mais aucune écriture canonique n'est synthétisée tant que le tuple persistant `identity_id/account_id` est incomplet. Les contrôles de propriétaire canonique restent en lecture seule. Une ligne déjà liée ne perd pas son owner sur un patch partiel. Le miroir est **encore actif** pour les comptes Kobo liés, et pour les autres fournisseurs. | La tentative de couper W1 d'un coup a provoqué **124 échecs et 11 erreurs** dans la CI initiale (rapprochement manuel W4, bibliothèque et reprise), car ces composants utilisent encore le miroir historique. La PR est volontairement réduite : une coupure globale n'est possible qu'avec migration/reprise et remplacement W2/D1–D5. Le format des archives 0.7 reste consultable et le rollback de l'ancien schéma inchangé. |
| W2 | `core/kobo_backup.py` — `_refresh_manifest_record`, `_skip_manifest_item`, `_recover_local_remnants` et boucle de livraison 0.7 | Appels directs à `storage.backup_put` (manifest, archivage, reprise, réparation requise, réception/erreur) **uniquement pour les éditions encore sans SourceReference canonique** : les références typées sur le credential/édition interdisent la reprise, les mutations du manifest et le GET historique, même avec ABI partielle ; compte/identité alternatifs sont un veto conservateur. | `LibraryService.sync_kobo_backup`, synchronisation ciblée et reprise legacy restent exécutables pour le legacy pur sans référence typée. **Une session avec ABI v1/v2 complète est bloquée**. L'interruption historique demeure conservée, sans conversion en droit ni preuve de placement. **W2 durci** : une tentative `acquiring` sans fichier reconnue au scan devient `pending_acquisition` + code fermé `interrupted_acquisition_review` et **ne déclenche plus de GET** lorsque le même item est encore annoncé au manifeste ; le libellé historique de l'interruption conserve également ce veto. Un `pending_acquisition` simple issu d'un adaptateur indisponible avant toute tentative n'est pas bloqué. La révision historique n'est pas remplacée par celle du manifeste avant réconciliation. Reprise explicite, qualification des octets éventuels et migration canonique restent à implémenter. |
| W3 | `Storage.backup_prune_missing` / ancien `_prune_successful_manifest` | **RETIRÉ pour Kobo : zéro suppression, même sur manifeste complet, et API de compatibilité sans accès SQL**. Les autres fournisseurs gardent leur prune de projection. | Aucune entrée Kobo atteignable : scheduler retiré, compatibilité retourne zéro sans transaction. Toutes les lignes v0.7 sont archivées jusqu'à migration explicite (même `pending` sans indices). Tests `tests/test_core_p0_3196_w3_prune_retired.py` et contrats W3 existants. |
| W4 | `core/kobo_local_reconciliation.py::reconcile_kobo_local` | **API positive retirée avant IO** : preview/confirmation/undo publics, CLI, CoreUI et façade offline refusent les mutations 0.7. | **Exécuteur historique déplacé hors du Core livré** dans `tests/kobo_legacy_reconciliation_archive.py` (fixtures rétrospectives seulement). Restent les tests d'annulation/relink canonique et l'intégration W1/W2 ; des octets vérifiés ne créent aucun droit. |
| W5 | `Storage.relocate_local_path`, `Storage.commit_directory_migration` et relocalisations historiques | Les relocalisations ordinaires et migrations de répertoire conservent désormais `backup_items.local_path` et `updated_at` **pour toutes les archives Kobo 0.7**, même sans SourceReference typée ; elles ne déplacent que les Placements canoniques admissibles. **La migration de répertoire refuse désormais de fusionner deux Placements au même chemin cible si propriétaire, Artifact, état ou portée historique/canonique divergent** : seule la déduplication de deux Placements `present` locaux sans fournisseur, même SHA Artifact et aucune référence `backup_items` / `origin_links`, est conservée. Les vrais déplacements journalisés de répertoire (sans collision de Placements) restent possibles. | Les migrations de chemins ne prouvent pas les droits : les relinks canoniques disposent d'un opérateur distinct avec OriginLink/SourceKey/compte attestés. W5 n'est pas archivé globalement et les cas de collision avec preuve devront disposer d'une migration spécialisée. |

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

**Complément W1 (07/11/2026) — [#4929](https://github.com/pzia/cifereca/pull/4929) FUSIONNÉE :**
pour les états `reconcile_required` et `reconcile_conflict`, **même avec SHA
et taille historiques syntaxiquement valides**, aucune projection positive
d'entitlement, acquisition, Artifact, Placement ou catalogue n'est désormais
permise **sans** connexion Kobo **activée** portant exactement le
`credential/identity` de l'archive. L'enregistrement 0.7 reste consultable,
le contrôle négatif de propriétaire reste transactionnel et les droits
canoniques préexistants ne sont pas effacés. Une connexion exacte et active
conserve provisoirement son miroir pour la découverte OWN de W2 tant que
l'ABI/les publications typées ne sont pas complètes. Ce n'est ni un droit
inventé par les octets, ni un retrait global du miroir W1.
Contrat : `docs/CORE-P0-3196-W1-DISCONNECTED-RECONCILIATION.md`.
Tests : `tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py`.

**Complément W1 (07/11/2026) — archive des refus fournisseur et tentatives interrompues :**
les checkpoints 0.7 portant `attention_code=archived_book` (Kobo 409),
`missing_delegation_scope` (portée interdite) ou
`interrupted_acquisition_review` (tentative sans reçu indépendant) ne
déclenchent plus de projection `entitlements/acquisition_ledger/artifacts/placements/catalog_entries`,
**même lorsque le credential est encore activé et l'ancienne archive contient
un SHA/taille complet**. Le refus est contrôlé sur le checkpoint fusionné
et n'efface ni la ligne 0.7 ni un droit Core préexistant. Les autres statuts
Kobo de source active restent temporairement sur W1 jusqu'au cutover A→B de W2.
Tests : `tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py`.

**Mise à jour W3 (06/11/2026) :** l'intégralité du prune Kobo est retirée, y compris pour les enregistrements `pending` sans aucune preuve ; les détails historiques de garde W3 ci-dessous décrivent les protections antérieures désormais subsumées par la conservation de *toutes* les lignes. La migration transactionnelle seule pourra décider du retrait d'une archive.

**Cutover W2 après manifeste — scope de session exact (07/11/2026) :**
la vérification de l'émetteur Kobo ne s'arrête plus à la réponse
`backup_manifest()` : le traitement de chaque édition contrôle le même
objet session/compte/epoch et propriétaire SQLite, y compris après les
callbacks de progression et métadonnées. Les écritures `backup_put` des
trois étapes `_refresh_manifest_record`, `_skip_manifest_item` et
`_acquire_manifest_item` passent toutes par cette garde ; le contenu GET
est revérifié juste avant son lancement. Si le compte change pendant
un GET déjà entamé, **aucun reçu `complete` ou `error` n'est attribué
au nouveau compte** : le checkpoint `acquiring` reste pour reprise
manuelle après revalidation et aucun second GET automatique ne démarre.
Le batch s'arrête en `aborted`, sans inventer de droit ni d'OriginLink.
Le chemin de compatibilité sans ABI A→B demeure, ce garde ne constitue
pas le cutover fonctionnel final. Recette synthétique :
`tests/test_core_p0_3196_w2_item_session_scope.py`.

**Complément W2/W3 — orphelins avec indices d'octets :** un ancien enregistrement Kobo sans `local_path` mais avec `sha256` non vide et/ou `size` positive reste consultable après un manifeste sans l'édition. Ce sont des indices historiques, pas des fichiers attestés ni des droits. À la réapparition de l'édition, W2 reconnaît aussi `error`/`pending` portant ces indices, **fige la révision acquise**, conserve SHA/taille et impose `reconcile_required` sans GET automatique. Ce garde de conservation/quarantaine n'efface rien et ne réalise pas le cutover ; tests SQLite et service sans fournisseur réel dans `tests/test_core_p0_3196_w3_orphaned_byte_evidence.py`.

**Complément W3 — refus de délégation :** `backup_prune_missing` conserve désormais un refus `attention_required` avec `attention_code=missing_delegation_scope`, même sans chemin local lorsque l'édition sort temporairement d'un manifeste complet. La disparition de l'édition ne démontre pas un nouveau scope ; le retour du manifeste ne doit jamais réarmer implicitement le GET refusé. Seule une reprise explicite du compte ou un cutover canonique attesté peut lever ce checkpoint ; test SQLite et synchronisation sans réseau dans `tests/test_core_p0_3196_w3_scope_denial_retention.py`.

**Historique des gardes W4 (fixtures archivées uniquement) :** la
transaction 0.7 refusait un Placement dont l'Artifact possède un OriginLink
canonique de la même identité. Ce garde reste testé par l'archive
`tests/kobo_legacy_reconciliation_archive.py` mais **n'est plus une voie de
production** : `reconcile_kobo_local` refuse maintenant tout preview, toute
confirmation et tout undo 0.7 avant I/O (#4924/#4928). Seuls l'observation
physique **sans droit** et le relink d'un B déjà indépendamment attesté
conservent une API exécutable distincte.

**Gel de surface :** le test
`tests/test_core_p0_3196_residual_writer_surface.py` détecte les nouveaux
appels Python directs `.backup_put()` / `.backup_prune_missing()` hors de
`core/kobo_backup.py`. Il ne prétend **pas** détecter toute écriture SQL
dynamique (W4–W5) ; une revue de code de celles-ci reste indispensable.

**W5 — migration des répertoires de profils et propriétaire des archives
(07/11/2026) :** avant toute réécriture SQL des Placements/catalogues,
`commit_directory_migration` refuse de réécrire le `local_path`
d'une archive Kobo située dans le dossier déplacé si son `identity_id`
n'est pas **exactement** le propriétaire indiqué par la migration. Ancien
propriétaire nul, ancien nom de dossier prétendu propriétaire, traversée
lexicale `..` et budget supérieur à 100 000 archives empêchent
la transaction entière ; les autres dossiers et les archives au profil
exact restent inchangés / relocalisables. Aucun propriétaire ni droit
n'est dérivé du nom du chemin ou de l'empreinte. Tests SQL transactionnels
`tests/test_core_p0_3196_w5_directory_foreign_archive.py`. Ce veto
ne remplace pas la suppression de l'autorité historique W5 après le cutover
#4858/#4859.

**W5 — retrait des réécritures de localisateur archivées Kobo
(07/11/2026) :** `Storage.commit_directory_migration` **et**
`Storage.relocate_local_path` conservent désormais **exactement**
`backup_items.local_path` et `updated_at` d'une ligne Kobo 0.7
pendant un changement de dossier de profil, d'entrée de fichier ou
un renommage local direct. Une ancienne
déclaration de chemin ne prouve pas l'existence d'un nouveau fichier :
c'est un reçu historique, pas un Placement canonique. Les autres
fournisseurs conservent la réécriture compatible de leurs chemins ;
les Placements/metadata de catalogue suivent leurs contrats propres.
Les veto négatifs W5 contre autre identité, SourceReference déjà typée,
collision de Placement ou `dedup_cleanup_pending` restent en vigueur.
Cette coupure ne supprime aucune archive ni ne crée SourceReference,
OriginLink, Artifact ou droit. Recette mixte
`tests/test_core_p0_3196_w5_kobo_archive_path_immutable.py`,
plus adaptation des assertions W5 existantes. La relocalisation
d'un B déjà attesté est une opération distincte et confirmée (#4859).

**W4 — historique du rapprochement 0.7 (tests archivés seulement) :**
la transaction manuelle anciennement confirmable conservait, dans la **même
transaction SQLite** que son journal de confirmation/undo, une observation append-only
`core.kobo.local-file-evidence.<operation>` contenant la portée exacte
identité/credential/compte/édition/révision ancienne, le SHA/taille
réellement relus dans le répertoire du profil et le fingerprint
`device/inode/mtime/ctime`. Le preview ne crée rien, un échec du second
INSERT annule toute la confirmation et l'undo conserve cette trace
historique (le journal principal indique `undone`). Les anciens journaux
W4 sans nouvelle observation restent réversibles **dans les fixtures de
régression historiques** ; ils ne sont plus annulables par une entrée publique
0.7. Une transaction W4 historique ne
peut désormais **plus écraser un ledger 0.8 qui n'est pas explicitement
le miroir historique** (`metadata_json.legacy_status` unique).
Cette observation **ne prouve ni droit Kobo, ni SourceReference, ni
OriginLink, ni réception de téléchargement**, et ne permet aucun GET :
une migration canonique future devra toujours obtenir A fournisseur frais,
révision et reçu de livraison indépendants. Les états `reconcile_required/conflict` conservent encore leurs dépendances
**W1/W2**, pas l'exécuteur W4 retiré. L'opérateur distinct
`observe_kobo_local_file_only` journalise uniquement des octets observés et
ne crée aucune origine fournisseur ; **la coupure globale W1/W2 reste ouverte**.
Réception : `tests/test_core_1345_confirmed_local_reconciliation.py`.

## 2. Lecteurs qui influencent encore une décision

| ID | Point de lecture | Effet restant / dépendance |
| --- | --- | --- |
| D1 | `core/kobo_backup.py` — `backup_get/list`, `_skip_manifest_item`, `_recover_local_remnants`, `kobo_backup_preview` | **L’aperçu Kobo 0.7 ne classe plus une édition avec `SourceReference` typée (credential/édition exacts, même autre profil/emprunteur) comme `already_local_linked`, `already_local_verified`, `candidate_unverified` ou `potentially_missing`** : elle relève de `attention_required` (sauf archivage **explicitement fourni par un manifeste rafraîchi** : `skipped_archived`), sans attester B ni autoriser un GET. Les éditions purement historiques conservent la compatibilité. Les autres décisions internes `unchanged`, `reconcile_required`, skip et admission dépendent encore du 0.7 : le garde de chemin #4546 vérifie l'owner mais **ne prouve pas un OriginLink canonique**. |
| D2 | `core/source_materialization.py` — `_evidence()`, `completed()` | Aperçu/confirmation legacy, empreintes d'historique et décompte post-exécution. Bloqué si ABI Core complète **ou si le manifeste contient une édition ayant déjà une `SourceReference` Kobo pour le même credential, même autre borrower/profil** : la préparation refuse toute confirmation 0.7 et l'exécution revérifie après token. Sans référence typée, cette voie dépend encore de la table historique. |
| D3 | `services.py::_acquire_direct_item_impl` | `backup_get` et chemin local contribuent au veto de téléchargement manuel Kobo historique. **Une ABI complète ou une `SourceReference` typée sur le credential/édition (même autre borrower/identité) désactive désormais ce GET 0.7**, avec relecture sous verrou après admission ; l'UI ne propose plus cette action sur cette édition. Le legacy sans référence typée reste actif : **en l'absence d'une admission Core fraîche**, le D3 sous verrou refuse tout pointeur 0.7 `local_path` (même disparu), SHA/taille, ancienne acquisition `complete/acquiring/reconcile*`, interruption documentée, archive 409 ou refus de délégation 403 avant un autre GET. Cela ne prouve ni droit, ni présence B ; `pending` jamais commencé et sans indice reste admissible à l'action explicite. Une **admission Core actuelle**, déjà qualifiée par `owned_library_download`, conserve priorité : un vieux statut ou chemin 0.7 ne doit jamais opposer un veto à un droit fournisseur indépendamment attesté. Les tests couvrent les deux branches. Voir `tests/test_core_p0_3196_d3_legacy_recovery_checkpoint.py`. |
| D4 | `core/source_materialization_schedule.py`, `core/sync_coordination.py`, façade `services.py` | Les préférences exact-source et le checkpoint pilotent toujours la **planification** de W2 en l'absence d'ABI complète. L'ancien booléen global n'autorise pas un GET. |
| D5 | `core/kobo_local_reconciliation.py`, façades CLI/CoreUI/offline | **Retiré en production pour les opérations W4 legacy.** Les lectures/file-only et relinks d'un B déjà canonique restent distincts. Le writer archivé n'est plus un lecteur décisionnel appelable par les façades ; il reste utilisé exclusivement par des tests historiques. |
| D6 | `core/kobo_local_audit.py`, `kobo_backup_diagnostic`, `kobo_backup_preview`, `kobo_backup_records`, `core/backup_projection_audit.py` et `core/legacy_backup_migration_inventory.py` | Consultation/inventaire/admission à revue **sans preuve d'origine B**. Ces lecteurs peuvent rester pour l'archive, à condition de rester strictement passifs. |
| D7 | `core/library_projection.py::local_placements_for_sources` (recherche publique) **et** `local_placement_for_source` (cible individuelle) | **Cutover lecture typée :** si une `SourceReference` Kobo existe pour `credential/edition`, aucun des deux lecteurs n'infère un fichier local depuis un vieux `acquisition_ledger.complete`. L'alias public sans compte exige une SourceReference unique du profil, un OriginLink fournisseur et un Placement ouvrable sous l'identité ; toute deuxième source/droit/emprunteur bloque l'alias. Le lecteur à compte exact sélectionne la seule SourceReference du bon borrower, son OriginLink et le même Artifact, puis conserve sa vérification physique SHA/taille du fichier. Si l'OriginLink manque, les octets sont absents ou le scope est ambigu : aucun match. Les éditions non typées continuent la projection legacy en lecture seule, sans création de droit. |

**Cutover D1 — aperçu après manifeste frais :** lorsque la source
fournisseur vient d'être rafraîchie, un `backup_items.complete/ok`
et son `local_path` ne suffisent plus à produire
`already_local_linked` ou `already_local_verified`, **même si le SHA
et la taille du fichier correspondent à l'ancienne ligne**. La ligne est
`needs_reconciliation`, sans permission de GET ni fabrication d'un
OriginLink. La commande CLI **sans** `--refresh-manifest` utilise maintenant
`offline_canonical_only=True` et inspecte uniquement B depuis SQLite,
avec compte/emprunteur propriétaire exacts (même `account_id=""`),
sans connexion Kobo ni accès `backup_items`. Ses compteurs de droit actuel
et d'autorisation de GET restent nuls. Seuls les autres appelants
**internes** de `kobo_backup_preview` conservent provisoirement les
catégories 0.7 hors rafraîchissement : ils n'attestent pas B. Le préflight
**interne** de W2 (qui fournit `_item_reasons` pour décider de l'exécution)
conserve temporairement l'ancien classement : changer sa sémantique sans
migrer son contrat provoquerait un refus `reconciliation_required` sur
des téléchargements explicitement confirmés. Ce cutover vise **uniquement
la vue publique de diagnostic**. Les éditions déjà typées restent soumises
au garde SourceReference antérieur. Ce changement ne retire ni le lecteur
legacy, ni W2/W4. Test :
`tests/test_kobo_backup_preview.py`.

**Cutover D1 — compteur « Kobo locaux non reliés » :** après publication A/B canonique, l'archive `backup_items` peut être retirée par une migration exacte, sans que ce fichier ressurgisse comme « orphelin ». `kobo_unlinked_local_count` reconnaît désormais une exception **uniquement** si le profil possède un Placement `present`, un Artifact dont les octets actuels SHA/taille/inode sont revérifiés sous le répertoire enregistré, un OriginLink `provider` et une SourceReference Kobo `provider_response` active pour un compte actuellement rattaché et activé. Ni le nom du fichier, ni la fiche catalogue ni une origine d'un autre profil ne suffisent. Le lecteur historique `backup_items` reste temporairement consulté pour les éditions sans provenance canonique ; le cutover D1 total dépend encore de W2/W4. Test `tests/test_core_p0_3196_d1_canonical_unlinked_count.py`.

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
5. **Achever W2/D1–D4 ; conserver W4 comme archive non exécutable en production.** L'API positive W4 et les façades sont coupées ; une entrée sans ABI v2 et toujours
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

**État du 07/11 : NON atteint.** W3 est retiré pour Kobo et **W4 n'a plus de writer public positif**, même en ABI partielle. W5 conserve désormais les localisateurs d'archives Kobo inchangés ; les migrations de Placements restent contrôlées. W1 bloque le miroir pour les reçus incomplets et les rapprochements sans connexion active exacte, mais **la projection 0.7 sur source active demeure**. Les éditions historiques sans SourceReference n'ont pas migré : **W2 et D1–D4 internes restent le chemin critique**, pas l'undo W4 déjà retiré. Les garde-fous ne remplacent pas le cutover canonique A→B.
