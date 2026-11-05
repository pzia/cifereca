# P0 #3196 — Matrice d'accessibilité du writer Kobo historique (05/11/2026)

## Autorité et portée

**Source de vérité du diagnostic :** `main` après PR #4494 et #4495.
Cette matrice décrit l'**accessibilité** du chemin de compatibilité
`core/kobo_backup.py` vers `Storage.backup_put()`. Elle ne prouve ni
exécution ni droits d'une session Kobo réelle et ne migre aucune ligne SQL.
L'ABI canonique est testée **statiquement, sans I/O**, dans
`core/canonical_delivery_abi.py::has_complete_delivery_abi` :

- **v2 complet** : `downloadable_resource_snapshot`,
  `downloadable_selected_offer_snapshot`, et **une** livraison v2
  `downloadable_selected_delivery_candidate_runtime_guarded`,
  `downloadable_selected_delivery_candidate_guarded` ou
  `downloadable_selected_delivery_candidate`. Les deux formes protégées
  peuvent être les **seules** méthodes de livraison : le Core sait leur
  fournir le lease de consentement vivant, et la compatibilité 0.7 doit
  donc déjà leur refuser un second writer ;
- **v1 complet** : `rights_observation_snapshot`,
  `rights_selected_delivery_offer_snapshot`,
  `rights_selected_delivery_candidate` ;
- **partiel** : une ou deux méthodes de chaque trio, sans trio complet ;
- **absent** : aucun trio. Une combinaison inter-ABI (v1+v2 incomplète)
  n'est pas assimilée à une ABI complète.

Il suffit qu'**un** trio complet soit statiquement callable pour refuser
la compatibilité : cela ne signifie pas que le compte, le droit ou la
livraison soient automatiquement autorisés.

## Matrice des entrées encore atteignables

| Voie | ABI absente / partielle | v1 complet / v2 complet | Admission et preuve résiduelle |
| --- | --- | --- | --- |
| `LibraryService.sync_kobo_backup` (CLI `kobo backup`, contrôleur historique) | Chemin `kobo_backup.sync_kobo_backup → _sync_kobo_backup_locked` encore exécutable **si compte connecté et capability backup** ; le flux peut écrire `backup_items`. | Refus `kobo_canonical_source_delivery_active` avant catalogue, manifest, ADEPT ou `backup_put` ; pour un batch non scindé, un compte complet bloque tout le batch. | Verrou d'acquisition partagé, admission locale et autres contrôles 0.7 restent requis pour les comptes anciens ; ne jamais transformer un `backup_items.complete` en présence canonique. |
| `source_materialization` (preview puis confirmation d'achat) | Vieux préflight + token de confirmation et `kobo_backup._sync_kobo_backup_locked(confirmed_manifest=...)` encore accessibles pour un compte explicitement admissible ; droits/manifeste, digest et scope revalidés. | `_source` refuse `canonical_source_delivery_active` **en preview comme à l'exécution**, y compris si ABI complétée entre les deux. | Token volatile, refus d'ambiguïté, compte/credential exact et lock. Le retour ne doit pas publier une deuxième provenance B. |
| `source_materialization_schedule` (on-start) | Préférence par source `sync.source.materialization.on_start.*` et paramètres de retry peuvent permettre le vieux lancement, après contrôles de connexion/compte. | `_configured_binding` refuse toute ABI complète : le préférentiel ancien reste en stockage mais ne programme aucun writer 0.7. | Aucun ancien `kobo_backup.enabled` global n'accorde automatiquement le consentement. |
| `LibraryService.can_direct_download / _acquire_direct_item_impl` | Parcours manuel de compatibilité encore conditionnel après droits exacts et scope. | L'ABI complète exclut le direct legacy ; le garde d'exécution recontrôle sous verrou avant un GET. | Un simple bouton/objet de catalogue public ne prouve pas un droit. |
| `kobo_backup_preview` et diagnostic local | **Lecture seule** des `backup_items` et option de rafraîchissement distant du manifeste (pas de contenu). | Même lecture possible **à titre consultatif**, jamais autorité de placement ou déclencheur de GET. | Les états `already_local_linked`, `reconcile_required`, `complete` 0.7 restent historiques. Un fichier est présent seulement sous OriginLink exact + SHA + Placement actuel du profil. |
| Reprise `acquiring` / rapprochement ADEPT historique dans `kobo_backup` | Reste accessible **uniquement** par le flux de compatibilité admis ; peut corriger les lignes historiques `pending_acquisition`, `reconcile_required`, `attention_required` et écrire le baseline ancien. | Le writer legacy est refusé : la reprise doit se faire par `canonical_recovery`, reçu durable et admission du moteur unique, **sans second GET induit par un reçu historique**. | Ne pas effacer le dernier mécanisme de reprise 0.7 avant une migration prouvée des lignes réelles compatibles. |

## Points d'écriture historiques encore présents

Le pipeline canonique `canonical_publication` / `convergence_execution`
n'appelle pas `backup_put`. En revanche, le chemin **encore atteignable**
`core/kobo_backup.py` contient des appels `self.storage.backup_put`
pour la récupération après interruption, le rafraîchissement du manifeste,
les statuts d'archive, l'invalidité ou le changement SHA/size d'un fichier,
les placements absents, les collisions et les déclarations incertaines.
Un appel `backup_put` demeure aussi sur l'acquisition 0.7 après admission.
Ces appels sont **des branches conditionnelles du même writer**, et non
autant de nouveaux endpoints ou permissions.

Les refus d'ABI v1/v2 complète sont déjà documentés/testés notamment dans :

- `tests/test_core_p0_3196_kobo_direct_writer_fence.py` ;
- `tests/test_core_p0_3196_kobo_direct_item_single_writer.py` ;
- `core/source_materialization.py` et
  `core/source_materialization_schedule.py`.

**Attention :** une ABI **partielle** n'est pas une preuve de continuité du
vieux mode : son aptitude effective à sauvegarder dépend de la session,
du manifest, du compte, de Kobo-Adept et des contrôles d'acquisition 0.7.
La matrice classe seulement la possibilité d'atteindre le writer.

## Contrat de migration encore manquant — non implémenté ici

1. Inventorier les lignes `backup_items` sous exact
   `identity/provider/credential/account/item/revision`, statuts
   `acquiring|reconcile_required|complete|skipped_archived`, reçus et chemins.
   Une ligne ancienne ne doit jamais devenir un droit attesté par simple
   titre/ISBN/SHA. Les `complete` sans preuve canonique demeurent historiques.
2. Pour chaque candidat, vérifier source, compte actif, origine déjà publiée
   ou reçu terminé conforme, octets/inode/SHA sous la vraie racine de profil,
   état de staging et absence de collision. Refuser sources multi-comptes,
   chemins ambigus, JSON malformé et éléments non couverts par le budget.
3. Migrer en transaction/reprise **idempotente et non destructive** vers le
   modèle canonique seulement ce qui est prouvé. Conserver les preuves
   ADEPT, l'ancien baseline consultatif et les autres profils intacts.
4. Mettre les consommateurs en **projection unidirectionnelle read-only**
   une fois le substitut de reprise vérifié. **Ne pas supprimer
   `backup_items` ou le dernier writer aujourd'hui.**
5. Gater sur fixtures SQLite/FS : variations `missing/partial/v1/v2`,
   crash avant et après reçu, archive, fichier déplacé, collision SHA,
   comptes croisés, replays de Sync idempotents et installation/rollback.
   Aucun nouveau test massif du compte Kobo 522 n'est nécessaire.

Cette tranche est un **audit de routage** et une condition préalable de
migration ; elle n'annonce ni suppression des writers ni clôture de #3196.
