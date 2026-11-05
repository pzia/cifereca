# P0 #2847 — Fichier revenu au chemin d'origine (orphan uniquement)

## Problème

Une ligne `placements.state='missing'` reste parfois marquée absente
après le retour d'un montage ou du fichier déplacé. Depuis #4550,
`acquisition-audit --verify-files` rend
`placement_state_unverified` si le SHA a été physiquement retrouvé :
**ce résultat d'audit ne réactive jamais automatiquement un Placement**.
La relocalisation `old_path → new_path` n'est pas adaptée quand le fichier
est revenu sous le **même** chemin ; la retraite refuse à juste titre un
fichier existant.

## Primitive Core privée (deux opérations)

```python
from cifereca.core.missing_placement_same_path import (
    preview_reappeared_missing_placement,
    restore_reappeared_missing_placement,
)
```

- L'**aperçu** ne modifie rien. Il exige un unique propriétaire enregistré,
  un `Placement` `missing` sous sa racine physique exacte, un `Artifact`
  SHA-256 + taille valides, et un fichier régulier identique, revérifié
  sans suivre les liens symboliques des dossiers et du fichier.
- **Cette première tranche est limitée aux Placements orphelins.** Tout
  catalogue directement lié, `backup_items` référent, reçu,
  déclaration, **clé de `metadata_json` autre que `relative_path` vide
  ou nul**, ou autre dépendance recensée est un **veto**.
  `provider_id` et `source_item_id` préexistants d'un Placement orphelin
  sont seulement des **étiquettes historiques** : leur conservation n'atteste
  ni droit fournisseur ni `SourceReference` / `OriginLink`. Ils ne peuvent
  déclencher d'acquisition ou de rattachement source.
  Les Placements liés demandent le mécanisme de rapprochement spécialisé :
  jamais un simple changement de `state` par corrélation SHA.
- Le `plan_token` privé est l'empreinte du scope, du contenu SQLite,
  des inodes/montages et du fichier. Il n'est **pas** retourné au plugin ni
  à l'interface.
- `MissingPlacementOperatorSession.preview_same_path_restoration()`
  conserve le token **dans sa session Core privée**, après contrôle
  d'inspection complète, identité active et fraîcheur SQLite. La
  confirmation exige une deuxième action explicite.
- Le **commit** requiert le verrou global d'acquisition en cours, une
  transaction SQLite `BEGIN IMMEDIATE` + `synchronous=FULL`, puis
  revalide tout le dossier et le SHA sans suivre de liens. Il compare
  le token, opère un seul CAS `missing → present` et conserve chemin,
  propriétaire, Artifact, source, droits, OriginLinks et fichiers.
  Une modification concurrente, annulation, absence de fichier ou
  réexécution bloque et rollback.
- Seul `status='restored'` signifie ce CAS confirmé. Ni une lecture de
  manifeste, ni une offre fournisseur, ni un nouveau droit ne sont créés.

## Non inclus et reste à livrer

- Les Placements rattachés à un catalogue ou à des receipts/backup historiques
  ne sont **pas** réactivés ici : ils requièrent un plan de migration des
  références exactes et un contrôle positif de provenance (sans inférence
  depuis ISBN/titre/SHA).
- Le `LocalInspection` CoreUI expose maintenant un cas
  `stored_path_returned_historical` **uniquement** lorsque le fichier est
  physiquement constaté présent alors que le Placement SQLite est toujours
  `missing`. `preview_missing_same_path_restoration(case_key)` ne prend
  que la clé privée issue du scan complet ; les boutons de retraite ordinaires
  ne sont pas offerts pour ce cas, et `confirm_missing_repair` reste une
  seconde action révocable. L'interface GTK ne reçoit pas encore de nouveau
  bouton : ce raccordement UX sera séparé.
  Aucun opérateur réel n'est exécuté sur l'installation Numilog sans audit
  local et confirmation ciblée.
- Ce cas ne règle pas la compatibilité des déclarations locales **sans
  `placement_id`** : le contrat existant doit être arbitré séparément.
- #2847 reste P0 ouvert jusqu'à la recette terrain des Placements réellement
  manquants et au traitement explicite des cas liés.

Tests SQLite/FS synthétiques : retour physique, SHA, symlink, fichier absent,
dépendances, identité étrangère, fichier changé, CAS périmé, absence de verrou,
annulation, crash SQL et non-modification des droits/fichiers.

## Tranche Core complémentaire : catalogue lié exact (#2847)

L'opérateur privé `MissingPlacementOperatorSession.preview_linked_same_path_restoration()`
permet désormais une réparation **distincte** pour le fichier revenu **au
même chemin** lorsqu'une à 128 projections catalogue sont encore liées au
Placement `missing`. Le préflight ne produit un plan révocable que si :

- propriétaire/répertoire unique, Artifact SHA+taille exacts et preuve physique
  du fichier stable sous le répertoire du profil ;
- tous les catalogues ont l'identité, le provider, l'édition, l'Artifact et
  l'état `missing` du Placement, sans référence imbriquée ni chemin relatif ;
- aucun `backup_items.local_path` ne désigne ce chemin ; pas de métadonnées
  de Placement opaques ni de référence opérationnelle à son identifiant.

Sous verrou d'acquisition + transaction immédiate, le commit réévalue le
token, modifie **uniquement** `Placement.state=present` et
`catalog_entries.local_state=local`, puis contrôle de nouveau les octets
avant commit. Les titres, comptes, chemins, sources, droits et fichiers
restent inchangés. La restauration d'un orphelin reste interdite sur ces
catalogues liés ; l'opération nouvellement ajoutée ne traite **pas** les
reçus, backups, liens de provenance ou chemins relocalisés. Elle n'active pas
de nouvelle action GTK sans décision UX séparée. #2847 reste ouvert pour
les données réellement constatées.

## Tranche complémentaire : retour après indexation réelle du scanner (#2847)

La première implémentation de restauration liée (#4615) ne reconnaissait que
les métadonnées Placement vides. Or `LocalCatalog.index_path` écrit un
`Placement` avec `relative_path`, `artifact_kind='file'`, `file_size`
et `mtime_ns` ; `placements_mark_missing` conserve ces champs.
Cette incompatibilité rendait les fichiers **normalement indexés** impossibles
à réactiver via l'opérateur lié, même quand ils revenaient identiques.

La réparation liée accepte maintenant **exactement** cette enveloppe scanner
(aucune clé supplémentaire) lorsque `relative_path` est égal au chemin
lexical relatif à la racine configurée, `artifact_kind='file'`, taille
entière positive identique à l'Artifact, et `mtime_ns` entier positif.
L'ancien horodatage n'est pas une attestation du fichier revenu : la
preuve actualisée repose toujours sur `_file_proof` SHA/inode sans suivi de
symlink et sur le contrôle de profil, catalogue et transaction au commit.
La restauration orpheline conserve son veto pour toute enveloppe non vide.
Un bundle ou des métadonnées de droits / fichier partagé restent bloqués.

Aucun nouvel accès fournisseur, aucune mutation de droits et aucune action
GTK ajoutés. Tests : transition réelle `present → missing` du stockage,
retour du fichier, restauration de Placement et catalogue exacts, rejets de
métadonnées étrangères et relecture SHA, idempotence.
