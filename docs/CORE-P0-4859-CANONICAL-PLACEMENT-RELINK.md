# P0 #4859 — W4 : relocalisation d'un Placement Kobo canonique existant

Cette opération **remplace le rapprochement positif W4** uniquement dans le
cas où l'édition Kobo dispose déjà d'une SourceReference fournisseur
authentifiée, d'un OriginLink fournisseur B avec reçu, d'un Artifact SHA-256
et d'un Placement Core du **même profil**. Elle n'utilise jamais les colonnes
`backup_items` pour déduire un droit, un emprunteur ou une origine.

## CLI hors réseau

```sh
cifereca kobo-relink-canonical \
  --identity lecteur --credential compte-kobo \
  --account-id emprunteur-kobo \
  --item edition-exacte --right achat-exact \
  --branch purchases --placement id-placement-core \
  --path /bibliotheque/lecteur/livre-deplace.epub
```

La commande lit le profil et la base SQLite existants, **sans démarrer
Core, plugin ou fournisseur**. Elle renvoie un `plan_token` opaque sans
modifier aucun fichier ni enregistrement. Après revue, répéter les mêmes
arguments avec `--confirm <plan_token>`.

Le Core verrouille l'acquisition et effectue une transaction SQLite
`BEGIN IMMEDIATE` ; il vérifie :
- le compte Kobo exact et activé, ainsi que le répertoire propriétaire unique ;
- SourceReference fournisseur active et OriginLink B **déjà publié**, pour
  le même compte/édition/droit/branche et le même Artifact ;
- l'ancien chemin canonique inexistant, même s'il existe une symlink ;
- le nouveau chemin non revendiqué par un autre Placement, dans le profil
  déclaré, non symlinké, avec SHA/taille/inode/stat de l'Artifact ;
- le token exact et l'absence d'interversion de configuration, de
  propriétaire, d'origine ou d'octets avant commit.

**Seules** les colonnes `path`, `state`, `last_seen_at`, `updated_at`
du Placement existant sont mises à jour, avec un journal de l'opération.
Aucun `backup_items`, entitlement, ledger, Artifact, SourceReference,
OriginLink, catalogue ou fichier physique n'est créé, modifié ou supprimé.
Le répertoire de configuration utilisateur est relu avant commit.

Cette première tranche ne déplace **pas** elle-même le fichier : celui-ci
doit avoir été déplacé par l'utilisateur ou un outil externe ; l'ancien
chemin doit être libre. La commande n'est ni une adoption d'archives
historiques, ni une autorisation de GET, ni une création de B. Le retour
est expurgé (sans chemin, IDs personnels ou SHA).

Une annulation physique nécessite de remettre le fichier à l'ancien chemin
et de faire une nouvelle relocalisation vérifiée ; aucun undo opaque ne peut
réhabiliter une provenance devenue invalide. L'ancienne commande W4/undo
reste temporairement accessible pour son périmètre non migré.

Recette : `tests/test_core_p0_4859_verified_canonical_relink.py`
(origine A/B vraie, changement d'inode même SHA, rebind de config,
profil étranger, ancienne copie encore présente, source désactivée,
destinataire déjà revendiqué, symlink, triggers SQLite et rollback).

**Ne clôt pas #4859/#3196** : W4 historique et undo doivent encore être
remplacés ou explicitement retirés après les cas A/B non couverts.
