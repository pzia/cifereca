# Core P1 #3196 — audit de dérive backup_items → autorités SQLite

## Contrat livré

`cifereca.core.backup_projection_audit.audit_backup_projection(
storage, identity_id="...", max_rows=500)` lit **une seule identité**,
uniquement le fournisseur `kobo`, sans appeler un plugin et sans toucher au
système de fichiers, aux droits ni aux acquisitions.

L'audit confronte les clés de portée exactes
`(identity, provider, credential, account, item)` de `backup_items` aux
identifiants déterministes des tables `entitlements` et
`acquisition_ledger`, ainsi qu'aux `placements` portant le chemin
explicite. Il expose les compteurs fermés suivants : droits/ledger absents,
divergences de portée, de statut d'acquisition, d'artefact et de placement.

Les anciennes lignes Kobo sans `identity_id` rendent tout audit scoped **`scope_unverified`**, sans affectation implicite au profil courant à partir d'une credential ou d'un titre. Leur migration explicite doit précéder la qualification complète.

**Un statut `complete` signifie uniquement que l'échantillon SQLite a été
parcouru en entier**, et non qu'une acquisition est réussie, qu'un fichier
est accessible, qu'un achat est attesté ou que l'inventaire vendeur est
exhaustif. `budget_exceeded`, `scope_unverified`, `invalid_scope` et `unavailable` sont
des états **non concluants** ; pas de totaux partiels présentés comme preuves.
Budget maximal fixe : 500 lignes. Les champs d'identification, credentials
et chemins restent à l'intérieur de SQLite ; seules les valeurs de statut et
les sommes sont rendues.

## Raison d'être

Actuellement `Storage.backup_put` écrit la projection historique puis
alimente le ledger générique, mais la suite des opérations peut faire évoluer
le ledger sans changer le statut `backup_items`. Réconcilier à partir de la
projection v0.7 risquerait alors de reclasser une acquisition terminée comme
`pending`. Le présent audit **détecte** cette divergence ; il ne la résout
pas et ne modifie pas ce chemin d'écriture pendant la transition.

## Étapes restantes #3196

1. Attribuer une autorité canonique à chaque statut et disposition de
   sauvegarde, y compris `index_pending`, `reconcile_required` et les
   migrations historiques, sans assimiler les labels v0.7.
2. Remplacer les lecteurs Kobo/preview par des projections dérivées bornées,
   après comparaison sur les bases anciennes et les cas multi-compte.
3. Supprimer les écritures bidirectionnelles seulement après migration SQLite
   versionnée, récupération d'interruption et tests d'idempotence sur reprise.
4. Croiser la réparation de chemins avec #3185. Aucune suppression de table
   ou de fichier ne relève de cette tranche.

Aucune release ni mutation de bibliothèque réelle.
