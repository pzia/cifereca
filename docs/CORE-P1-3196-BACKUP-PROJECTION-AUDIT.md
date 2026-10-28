# Core P1 #3196 — audit de dérive backup_items → autorités SQLite

> **Document historique/partiel — 28/10/2026.** backup_items = projection historique v0.7, migration canonique #3196 en cours. La cible normative est la **synchronisation automatique unique** décrite dans [ARCHITECTURE.md](ARCHITECTURE.md) et [#3171](https://github.com/pzia/cifereca/issues/3171) ; le rattrapage manuel #2797 reste exceptionnel. Conserver les gardes existants ; ne pas déduire qu'un OriginLink canonique/transaction Core est livré de cette seule recette.

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

## Lot complémentaire : UPsert protégé contre la réattribution implicite (#3196)

`Storage.backup_put` conserve temporairement la compatibilité Kobo 0.7.
Sa clé `(provider_id, credential_id, item_id)` ne contient ni l'identité
Cifereca ni l'emprunteur exact. Une nouvelle exécution avec un **autre
propriétaire explicitement enregistré** ne peut désormais plus remplacer les
champs de ce livre puis propager le changement au ledger.

Les trois étapes d'UPSERT (`backup_items`, `entitlements`,
`acquisition_ledger`) vérifient leurs propriétaires **dans la commande SQL
atomique**. Toute collision entraîne un `ValueError` fermé et un **rollback
de la transaction** : ni projection, ni droit, ni acquisition partiellement
réattribuée. Les sauvegardes répétées du même propriétaire restent admises.
La reprise des lignes legacy sans identité explicite reste permise pour
compatibilité ; elle ne constitue pas en elle-même un droit d'acquisition et
leur qualification reste sous l'audit `scope_unverified`.

Cette garantie ne supprime pas les écritures de la projection v0.7 ni ne
migre les anciennes lignes ; elle prépare le retrait du double chemin
autoritatif. Les références `placements` et les chemins physiques demeurent
traités séparément par #3185. Les tests ne touchent aucun fournisseur Kobo réel.

## Lot additionnel : propriété d'un chemin de placement

La projection Kobo persistait historiquement le chemin physique par
`placements.path` unique. L'ancien `ON CONFLICT(path)` pouvait écraser
`artifact_id`, `provider_id` et `source_item_id` depuis une autre
connexion ou un autre livre, **sans preuve de transfert de propriété**.

Le miroir applique désormais une condition SQL sur l'identité, le fournisseur
et le livre source avant tout `UPDATE` d'un placement existant. Deux valeurs explicites différentes **ne transfèrent plus** la propriété
du placement. Les projections v0.7 conservent cependant les livres historiques
qui citent le même chemin : le second droit et son acquisition sont conservés,
mais **sans lien vers un artefact ou un placement canonique**, sans nouvelle
notice locale et sans mutation du placement déjà occupé. Le diagnostic de
réconciliation peut ainsi retrouver les preuves incohérentes sans inventer un
transfert ni interrompre tout l'inventaire. Les mises à jour idempotentes du
même propriétaire restent autorisées.

Un ancien `NULL` n'est pas réinterprété comme attestation d'une nouvelle
identité lors de cette projection : l'owner manquant reste à résoudre par
réconciliation explicite. **Aucun fichier** n'est déplacé, détruit,
téléchargé ou associé par SHA/titre. L'ownership des placements sera
traité complètement avec #3185 lors de la migration vers la source
canonique, pas par une affectation implicite Kobo.


## Tranche P0 : couverture inverse des acquisitions canoniques

L’audit lit désormais les deux directions dans la même transaction SQLite :

- `backup_items → entitlement / acquisition / placement` : détection des divergences historiques conservée.
- `acquisition_ledger → backup_items` : `canonical_scanned`, `canonical_without_projection` (reçu canonique sans copie v0.7), `canonical_projection_owner_conflict` (projection d’un autre propriétaire) et `canonical_projection_scope_unverified` (coordonnées incomplètes).

Chaque direction a son propre plafond de **500 lignes par identité**. Un dépassement donne `budget_exceeded` et tous les compteurs à zéro. Un reçu canonique sans projection est un fait de migration, pas un droit supplémentaire à créer et pas une raison de recopier `backup_items`.

Cette tranche est 100 % lecture SQLite : aucune écriture, suppression, copie, téléchargement, migration de schéma, mutation de prêt ni reclassement implicite. Le retrait effectif de la compatibilité v0.7, les consommateurs et la migration versionnée restent dans #3196.


## Tranche P0 : préserver le statut `complete` des replays passifs

La version initiale du garde refusait tout décalage entre la projection Kobo
0.7 et `acquisition_ledger`. Les parcours réels Kobo montrent au contraire
qu'un **téléchargement valide** fait progresser le reçu canonique avant
l'écriture de la projection v0.7 ; une interdiction globale interrompait
l'acquisition et la reprise.

Le miroir utilise désormais une condition SQL limitée : lorsque le reçu
canonique est déjà `complete`, un UPSERT v0.7 en état **passif**
`pending` ou `discovered` conserve le statut, l'artefact et les champs
d'erreur/attention du reçu canonique. La projection historique peut rester
en `pending` jusqu'à migration ; l'audit révèle cette divergence.

En revanche, les transitions **actives** `acquiring`, `attention_required`,
`retryable_error` et `complete` restent supportées pour la compatibilité
des flux Kobo et du traitement ADEPT. Aucun refus générique du passage
intermédiaire par un état divergent : la migration vers une source d'autorité
unique demeure nécessaire et distincte.

## Barrière d'écriture legacy sur les faits physiques canoniques (#3196)

Le miroir de `backup_put()` ne modifie plus un Artifact existant depuis une
simple déclaration SHA/taille/format. Il ne rafraîchit un Placement existant
que si celui-ci est encore un placeholder `unverified` portant exclusivement
la métadonnée d'import `backup_items`. Un scan ou une publication canonique,
y compris un Placement ensuite `missing` ou `invalid`, conserve ses octets,
son propriétaire, son état et son catalogue face au rejeu legacy. Les
SourceReference et OriginLink restent inchangés. La tentative historique est
conservée sans nouveau rattachement à cet Artifact dans le ledger.

Les tests exercent une vraie publication SQLite v14 suivie de rejeux de même
SHA et de SHA contradictoires, les états present/missing/invalid, un
placeholder canonique non issu de backup_items et la compatibilité du
rafraîchissement legacy non vérifié. Aucun réseau ni traitement ADEPT réel.
Cette barrière ne retire pas encore les writers et lecteurs backup_items :
#3196 reste ouvert jusqu'à leur migration et à la projection lecture seule.

## Handoff d'une mise à jour legacy partielle (#3196)

Après l'UPSERT de compatibilité, `backup_put()` transmet au miroir la ligne
**effectivement fusionnée dans la même transaction**, plutôt que son seul
dictionnaire d'entrée. Un patch sans compte/identité conserve ainsi le scope
connu, les options coalescées et le même identifiant canonique. Il ne crée
plus un deuxième droit/receipt à compte NULL ni ne contourne un conflit de
propriétaire préexistant dans le ledger. Si le miroir refuse ce propriétaire,
la modification de compatibilité est également annulée. Ce correctif ne
migre pas les doublons historiques et ne retire pas la projection legacy.
