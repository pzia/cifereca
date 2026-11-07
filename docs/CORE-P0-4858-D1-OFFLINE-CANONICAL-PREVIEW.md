# P0 #4858 D1 — aperçu Kobo canonique hors ligne, sans autorité 0.7

## Portée

Pour une session Kobo **connectée avec ABI Core A→B complet**, la commande
`kobo-resume-preview` *sans* `--refresh-manifest` appelle le diagnostic
`acquisition_audit.inspect_exact_source`, avec le tuple
`(identity, provider=kobo, credential, borrower)` exact. Elle ne lit
plus `backup_items`, ne contacte pas Kobo/Vault, ne résout aucun lien de
contenu, ne démarre aucun GET/ADEPT ni import.

Cet audit peut constater les **preuves B historiques**, et sur
`--verify-files` revérifier SHA-256, inode, dossier de profil, lien symbolique
et emplacement physique existant. Une SourceReference/OriginLink persistée,
même avec fichier actuellement présent, n'atteste **pas le droit fournisseur
courant A**. En conséquence :

- `manifest_refreshed=false`, `manifest_valid=false`,
  `requires_manifest_validation=true`,
  `acquisition_authorized=false` ;
- les compteurs d'**admission** `already_local_verified`,
  `potentially_missing` et `needs_reconciliation` restent à zéro :
  ni un achat « absent », ni une permission de nouvelle livraison ne sont
  inférés hors réseau ;
- `historical_canonical.recorded_rights` et
  `previously_published_files_physically_verified` sont purement
  descriptifs ; `inventory_truncated` indique une projection bornée
  (512 sources). Aucun identifiant privé ni chemin n'est exporté.

Avec `--refresh-manifest`, le contrat existant #4871 conserve la
lecture A fournisseur actuelle et le plan B vérifié, sans écrire dans
`backup_items`. Pour les sessions à ABI partiel, le comportement
historique demeure temporairement compatible ; c'est une autre tranche de
migration W2/D1 avant fermeture de #4858.

La revalidation du compte SQLite, de l'unique borrower session, du
credential, de l'époque Core et du provider se fait avant et après
la lecture des preuves B. Une reconfiguration pendant le SHA est un
refus expurgé.

## Recette

`tests/test_core_p0_4858_d1_canonical_offline_preview.py` :
session synthétique complète, SQLite v14, vraie publication
Artifact/SourceReference/OriginLink/Placement, vraie vérification SHA
d'un PDF, absence ou déplacement du fichier, compte réattribué,
connexion désactivée et doublon d'emprunteur. Les accès
`backup_get/backup_list` et manifest Kobo sont interdits dans la
recette. Le droit A courant n'est **jamais** inventé depuis B.


## D1 — aperçus CLI avec rafraîchissement explicite

`cifereca-cli kobo-resume-preview --credential <id> --refresh-manifest`
doit d'abord relier **en mémoire seulement** l'unique borrower exact du
credential à la session Kobo ABI complète, avec les mêmes gardes
Config/SQLite de `connect_kobo_canonical` que pour les commandes confirmées.
Ce raccordement n'accorde **ni consentement CAS ON ni permission de GET de
livre** : il permet seulement au lecteur A de connaître l'instance
`Account` déjà reliée à la session fournisseur et à SQLite. L'aperçu
observe le manifeste de droits A à la demande, compare B en lecture seule et
rend `preview_only=True, acquisition_authorized=False`. Aucun fallback vers
`backup_items.complete` n'est admis en présence d'une ABI complète.
Une source étrangère/désactivée/rebind ou une session ambiguë répond
`unavailable`, sans inventer un droit.

Sans `--refresh-manifest`, aucune connexion ni inventaire fournisseur
n'est autorisé ; l'aperçu historique reste uniquement un diagnostic
`preview_only`, jamais un plan d'acquisition. Une ABI partielle conserve
sa classification ancienne jusqu'à sa migration W2 distincte.

Recettes : `tests/test_core_p0_4858_targeted_kobo_account_activation.py`
(sur Core initialement local-only + SQLite v14, lecture A positive,
absence d'appel B/GET, refus profil étranger) et
`tests/test_kobo_backup_preview_cli.py` (aucune connexion offline).
