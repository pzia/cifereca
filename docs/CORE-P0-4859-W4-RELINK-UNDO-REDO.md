# P0 #4859 W4/D5 — relink canonique rejouable après undo

## Défaut

`relink_verified_canonical_kobo` utilisait initialement
`SHA256(placement_id,old_path,new_path)` comme identifiant de journal.
Après un relink A→B confirmé, un undo B→A écrit un second reçu
append-only ; refaire A→B avec les mêmes fichiers/chemins déclenchait une
collision sur l'**ancien** identifiant et annulait la transaction. Le
Placement ne pouvait pas suivre une nouvelle relocalisation légitime.

## Contrat corrigé

- Le jeton de preview inclut le **dernier identifiant d'opération commise**
  pour ce Placement, relu sous la même transaction SQLite. C'est un
  curseur anti-ABA indépendant des secondes `updated_at`.
- L'identifiant de chaque nouvelle opération inclut ce jeton exact
  (portée fournisseur A, origin B, preuve physique et curseur).
  A→B→A→B donne quatre reçus distincts, tous append-only.
- Chaque undo demeure lié au reçu immédiatement précédent, à
  SourceReference/OriginLink, au fichier vérifié et au pointeur exact.
  Un ancien undo ne peut pas s'appliquer au nouveau lien B.
- Un curseur absent est admis pour la première relocalisation ;
  un curseur de type/format invalide fait échouer la preview avant écriture.
- **Aucun GET, aucune création de droit, OriginLink ou Artifact,
  aucune modification de backup_items, aucun mouvement de fichier.**

Test SQLite/FS : publication canonique existante, deux allers-retours
avec même SHA et mêmes noms, quatre journaux uniques, refus du vieil
undo, provenance A→B et données historiques inchangées.

P0 #4859 et parent #3196 restent ouverts : ce jalon ne prétend pas
résoudre les acquisitions Kobo 0.7 ou le cutover W1/W2.
