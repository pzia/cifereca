# P0 #4858 / D2 — compte Kobo primaire vide dans la confirmation manuelle

## Défaillance et borne

L'identifiant du compte Kobo primaire est littéralement `account_id=""`.
Dans `core/source_materialization.py::_source`, la validation de la clé
`(source_id, identity_id, provider_id, credential_id, unique_id)`
imposait abusivement une longueur non nulle aux cinq champs. La source
primaire exacte était donc refusée `invalid_source` **avant**
l'aperçu de droits, y compris avec un ABI Core A→B complet. Ce n'est
pas un défaut de droit fournisseur.

La validation accepte désormais cet unique cas si
`Account.is_primary is True`. Les quatre autres coordonnées demeurent
non vides, typées, bornées et sans contrôles. Un compte secondaire
sans identifiant ou un identifiant non normalisé reste interdit.

## Invariants conservés

- Une seule source exacte par digest d'identité/provider/credential/account ;
  une session connectée, un compte correspondant, et connexion du propriétaire
  contrôlée dans SQLite.
- Sur ABI complet : aperçu de droits A, consentement CAS ON exact,
  fingerprint mémoire, jeton à usage unique et contrôle des époques ;
  la confirmation appelle **une seule synchronisation canonique Core A→B**.
- Ni `backup_items`, ni titre, ni fichier/SHA historique ne prouvent B
  ou n'autorisent un GET. Pas de retour au writer W2 0.7 si ABI complet.
- Après rebind d'emprunteur ou révocation du jeton : aucune confirmation.
  Le Core Sync reste responsable de sa vérification transactionnelle
  indépendante des droits, offres, octets et comptes.

## Recette

`tests/test_core_p0_4858_manual_canonical_materialization.py` :
aperçu puis confirmation de l'emprunteur primaire `""`, une seule
invocation Core, jeton non rejouable, compte secondaire vide refusé,
identifiant non normalisé refusé, changement d'emprunteur révoquant
le jeton. Aucun accès réseau réel ni téléchargement historique.

Ce correctif débloque D2 mais **ne ferme pas #4858** : le cutover W2
des sessions à ABI absente/partielle et les lecteurs D1–D4 restent à prouver.
