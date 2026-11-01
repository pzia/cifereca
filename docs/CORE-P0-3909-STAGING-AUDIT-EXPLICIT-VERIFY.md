# P0 #3909 — coût et contrat du rapport staging

`sync_report.audit_source_counts(..., verify_files=False)` était censé
produire une synthèse **sans vérification physique**. Pourtant, l'appel
interne à `receipt_staging_evidence` passait toujours `verify_files=True`.
Le rapport relisait et hashait les octets des fichiers Core staging et des
Placements historiques sous `acquisition-work`, alors même que
`physical_verification` annonçait `not_performed`.

## Contrat corrigé

- **Par défaut** (`verify_files=False`) : seules les métadonnées SQLite
  sont consultées ; aucun hash de fichier staging. Les reçus concernés
  sont `core_staged_unverified` ou `indexed_staging_unverified` ;
  `staged_sha_verified_count=0` (absence de preuve, pas absence de livre).
- **Contrôle explicite** (`verify_files=True`, notamment dans
  `acquisition-audit --verify-files`) : conservation du contrôle SHA
  borné, isolation du profil, refus des symlinks et comptage par
  `acquisition_id` exact.
- Aucune modification du GET, de l'autorisation, de l'identité, de la
  promotion, de la publication, de la réparation ou du nettoyage.
- Tests SQLite synthétiques avec plusieurs reçus et une fonction de hash
  remplacée par une exception dans le cas non vérifiant. Les compteurs
  deviennent vérifiés uniquement après demande explicite.

Cette optimisation évite qu'un rapport de synchronisation fasse des lectures
potentiellement importantes sur des EPUB/PDF déjà en attente de publication.
Elle ne qualifie pas les six EPUB Numilog réels sans données de terrain.
