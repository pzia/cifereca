# P0 #3121 — chaînage du premier ACSM Paris à la livraison canonique

Le premier `loan.borrow` Paris est confirmé et son ACSM est téléchargé
par la branche manuelle. Ce premier token est seulement **staged** ; le
chemin de Sync `canonical_selected_delivery` ne le consommait pas et
déclenchait un second GET d'une URL fournisseur potentiellement éphémère.

Le Core consulte maintenant le reçu privé `.initial-acsm-receipt.json`
uniquement pour un `SourceKey` **Paris/dilicom/loan** déjà admis,
sous verrou de convergence et source/consentement actuels. Il reobserve
le prêt exact via `session.loans(account)` (lecture d'inventaire, pas
d'emprunt ni GET ACSM), puis `acsm_cache.reuse_initial` vérifie le reçu,
le SHA, le TTL et le claim SQLite `loan.borrow` confirmé. Une copie
bornée `O_NOFOLLOW` et exclusive est placée dans le `run-*` canonique
et devient le candidat ACSM Core ; **aucun deuxième GET du token**.
La transaction ADEPT et la publication de l'EPUB/PDF restent inchangées.

Un reçu présent mais invalide entraîne un refus explicite, sans fallback
automatique sur un deuxième GET. Sans reçu, l'ancienne livraison
du prêt existant reste disponible ; elle nécessite une vérification
séparée des redirections/mutations fournisseur. Aucune reprise du claim
d'emprunt, aucun droit nouveau et aucun Artifact ACSM.

Tests sans fournisseur : `tests/test_core_p0_3121_canonical_first_acsm.py`.
Les validations CI/installation ne valent pas recette d'un prêt réel.
