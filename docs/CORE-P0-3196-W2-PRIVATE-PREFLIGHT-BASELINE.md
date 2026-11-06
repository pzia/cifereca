# Core P0 #3196 — W2/D1 : préflight privé sans baseline historique

La vue publique Kobo classe déjà les chemins historiques 0.7 en revue,
et le Sync (#4812) interdit depuis peu de transformer un ancien
`backup_items.complete` en `unchanged` en calculant le premier SHA depuis
les octets actuellement présents à ce chemin.

Cependant, le préflight privé de `source_materialization.prepare`
réutilisait `kobo_backup_preview(_item_reasons=...)` : sans contrôle du
SHA/taille **préalables**, il classait encore un reçu `complete/ok` avec
seul `local_path` comme `already_local_linked` (ou vérifié selon le
mode), ce qui ouvrait une confirmation W2 contradictoire avec le Sync.

Cette tranche utilise la **même** validation du baseline 0.7 dans les
deux parcours : SHA-256 historique de 64 caractères hexadécimaux et
taille positive entière. Lorsque la preuve manque, la raison privée
est désormais `needs_reconciliation`, avant toute lecture du fichier ;
le préflight source ne peut alors préparer un téléchargement héritier.
Le chemin, la révision, les éventuelles valeurs brutes historiques et
les octets restent inchangés, sans GET ou effacement.

Un baseline valide reste une simple **revendication historique** et
conserve temporairement le comportement W2 compatible, notamment
pour permettre la reprise explicite des anciens dossiers. Il ne
représente pas un droit fournisseur, un OriginLink ou un reçu
canonique A→B. Le cutover intégral W2 exige encore l'exécuteur typé.

Tests : `tests/test_kobo_backup_preview.py` (statuts `complete/ok`,
modes privé avec/sans vérification, SHA absent/corrompu, taille
absente/nulle, aucun hash de la cible, aucun GET et SQL inchangé).
