# #3275 — Reprise d'un envoi déjà publié

`materialize_to_identity` copie sous le verrou d'acquisition puis publie un
Placement et deux origines canoniques (`transfer`, `cifereca`). L'enrichissement
de la notice intervient ensuite. Une interruption entre ces étapes ne doit
ni créer une deuxième copie ni laisser la copie publiée invisible au catalogue.

## Publication et reprise

La même transaction SQLite que les deux OriginLinks écrit maintenant les
coordonnées descriptives du Placement et ses marqueurs de copie : placement
source, identité d'origine, transfert local, taille et mtime observés. La
reprise exige toujours le profil, le chemin, le SHA et une provenance de copie
exacte. Elle ne réattribue aucun fichier indépendant du destinataire.

Si la notice est absente, l'envoi répété répare cette projection à partir du
même fichier vérifié et du même Placement. Une notice déjà présente conserve
le chemin court idempotent. Aucune nouvelle origine ni copie n'est créée.

## Historique de provenance

Une nouvelle SourceReference interne contient un snapshot des origines déjà
enregistrées pour l'Artifact dans le profil donneur : IDs d'origine et de
référence, type, preuve et coordonnées fournisseur exactes lorsqu'elles
existent. Les références historiques restent celles du donneur ; elles ne
deviennent pas des achats ou des licences du destinataire. Une chaîne de
transfert conserve les IDs des références internes précédentes, sans dupliquer
leurs arbres de métadonnées.

Ce snapshot est figé au premier commit. Un achat ultérieur du donneur n'est
pas ajouté rétroactivement lors d'un nouvel envoi identique. Les anciennes
références sans snapshot ne sont pas complétées depuis un état courant qui
ne prouverait pas leur histoire. La référence reçue demeure interne et non
synchronisable : supprimer le fichier ne demande aucune restauration.

## Preuves et limites

`tests/test_core_p1_3275_live_canonical_transfer.py` utilise LibraryService,
SQLite v14 et les fichiers réels de fixtures. Il couvre :

- interruptions après commit canonique, avant enrichissement du Placement ou
  de la notice, puis reprise sans second fichier ni nouveau lien ;
- coordonnées descriptives initialement absentes et réparation de projection ;
- callbacks CoreUI « Envoyer » A→B et B→C, achat B indépendant au même SHA :
  un Artifact, trois copies/inodes distincts, huit OriginLinks, aucune licence
  ou droit transmis ;
- snapshot historique figé, répétitions idempotentes et suppression de C sans
  restauration par le scan ni perte des origines historiques.

Les widgets du test de callback sont simulés, les achats et reçus ADEPT aussi.
Ces tests ne certifient ni une recette GTK native ni un accès fournisseur réel.
Une interruption **avant le commit canonique** peut encore laisser une copie
orpheline sans attribution de droit : la reprise de cet intervalle n'est pas
prouvée par cette tranche. Les bundles et données historiques restent sur
leur parcours existant ; aucune migration spéculative n'est réalisée.
