# P0 #4859 — W4 : relocalisation d'un Placement Kobo canonique existant

Cette opération **remplace le rapprochement positif W4** uniquement dans le
cas où l'édition Kobo dispose déjà d'une SourceReference fournisseur
authentifiée, d'un OriginLink fournisseur B avec reçu, d'un Artifact SHA-256
et d'un Placement Core du **même profil**. Elle n'utilise jamais les colonnes
`backup_items` pour déduire un droit, un emprunteur ou une origine.

## CLI hors réseau

```sh
cifereca-cli kobo-relink-canonical \
  --identity lecteur --credential compte-kobo \
  --account-id emprunteur-kobo \
  --item edition-exacte --right droit-exact \
  --branch purchases --kind purchase --placement id-placement-core \
  --path /bibliotheque/lecteur/livre-deplace.epub
```

Le paramètre `--kind` sélectionne **exactement** la nature de la preuve fournisseur déjà publiée : `purchase` (défaut compatible avec les commandes précédentes) ou `loan` pour un prêt préexistant, par exemple `--branch loans --kind loan`. Le type figure dans la SourceReference et dans l'empreinte du plan ; une confirmation de prêt ne peut pas être réutilisée comme achat ni inversement. La relocalisation ne prolonge pas la durée ni la validité d'un prêt et ne crée aucune nouvelle preuve de droit.

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

## Annulation supervisée (D5) de la relocalisation canonique

La confirmation de relocalisation renvoie désormais `operation_id`, digest
opaque du journal de l'opération. **Restaurer d'abord physiquement le fichier
à son chemin d'origine** (sans jamais le supprimer). La commande n'effectue
aucun déplacement de fichier.

Relancer la commande avec les **mêmes coordonnées exactes** de source/compte/
droit/branche/Placement, en remplaçant `--path` par le chemin d'origine et
en ajoutant `--undo OPERATION_ID`. Comme pour l'aller : aperçu en lecture
seule, puis confirmation explicite `--confirm PLAN_TOKEN`.

Le Core vérifie transactionnellement que le journal initial correspond au
même SourceReference fournisseur, au même Placement et aux chemins de
l'aller, et que le Placement n'a pas été repris par une autre opération.
Un curseur transactionnel de la dernière relocalisation par Placement
empêche notamment le rejeu **A→B→C→B**, y compris dans la même seconde
SQLite. Ce curseur n'est ni un verrou durable ni une autorité de droit.
La source, l'OriginLink et l'Artifact **doivent toujours être attestés**
et le fichier retourné doit avoir les octets, taille et inode attendus.
L'ancien chemin SQL (chemin aller) doit être physiquement absent ; un
symlink, une copie encore présente, un chemin revendiqué par autrui, un
journal ancien/malformé ou une preuve modifiée provoque un refus.

Le commit est **une nouvelle relocalisation B**, inscrite dans un second
journal append-only (`undo_of`) : aucune suppression ni modification de
droit, SourceReference, OriginLink, Artifact, acquisition, archive 0.7 ou
fichier. L'opération initiale n'est jamais effacée. Une annulation ne
réhabilite pas une provenance devenue invalide. Un relink inverse manuel
sans `--undo` reste possible suivant son ancien contrat strict mais ne
prétend pas être l'annulation d'une opération identifiée.

Le retrait de l'ancien exécuteur W4 reste en place ; les autres cas non
attestés ne sont pas « réconciliés » par cet undo.

Recette : `tests/test_core_p0_4859_verified_canonical_relink.py`
(origine A/B vraie, changement d'inode même SHA, rebind de config,
profil étranger, ancienne copie encore présente, source désactivée,
destinataire déjà revendiqué, symlink, triggers SQLite et rollback).

**Ne clôt pas #4859/#3196** : W4 historique et undo doivent encore être
remplacés ou explicitement retirés après les cas A/B non couverts.

La recette complémentaire `tests/test_core_p0_4859_verified_canonical_kobo_relink.py`
vérifie aussi les droits concurrents, la suppression de l'archive de preuve,
les chemins de deux profils, le changement d'inode à SHA égal et l'atomicité
après échec de journal ou changement de contexte. Le contexte facultatif
`context_current` doit être **sans accès à Storage/SQLite** pendant le
`BEGIN IMMEDIATE` ; l'adaptateur offline relit uniquement le JSON de
configuration du profil, sans jamais réentrer dans le verrou SQL.
