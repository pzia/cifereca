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
  déclaration, metadata opaque ou autre dépendance recensée est un **veto**.
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
