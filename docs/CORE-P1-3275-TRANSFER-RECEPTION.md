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
- arrêt brutal réel d'un processus après le commit, puis réouverture SQLite
  dans un nouveau service : même inode destinataire, notice réparée, deux liens ;
- coordonnées descriptives initialement absentes et réparation de projection ;
- callbacks CoreUI « Envoyer » A→B et B→C, achat B indépendant au même SHA :
  un Artifact, trois copies/inodes distincts, huit OriginLinks, aucune licence
  ou droit transmis ;
- snapshot historique figé, répétitions idempotentes et suppression de C sans
  restauration par le scan ni perte des origines historiques.

Les widgets du test de callback sont simulés, les achats et reçus ADEPT aussi.
Ces tests ne certifient ni une recette GTK native ni un accès fournisseur réel.
Les bundles et données historiques restent sur leur parcours existant ;
aucune migration spéculative n'est réalisée.

## Copie achevée avant commit canonique

Pour un fichier source canonique, la copie temporaire indépendante est
flushée, fsyncée et rehashée avant toute publication du nom final. Un
checkpoint est alors committé dans le journal d'opérations existant
`acquisition_ledger`, sous le scope interne `cifereca/local` et avec la
stratégie `local_transfer`. Il conserve le source Placement, les profils,
le SHA, les chemins et le pin device/inode/taille/mtime de la copie créée.
Il n'a ni `entitlement_id` ni `artifact_id` de réception ; ses statuts
`local_transfer_staged/published` ne sont jamais un succès d'acquisition
fournisseur ou une preuve de présence.

Ensuite seulement, un lien exclusif depuis l'inode temporaire donne le nom
final à la copie B (jamais un hardlink depuis A). Le répertoire est fsyncé.
La publication canonique revalide toujours les fichiers et scopes puis écrit
Placement/SourceReference/OriginLinks dans sa transaction habituelle.

Une nouvelle action explicite « Envoyer » peut reprendre exactement cet
inode, avant installation du nom final ou après rollback/interruption de la
transaction canonique. Un fichier préexistant ou remplacé, même à SHA égal,
n'est pas adopté. Le fichier temporaire n'est supprimé qu'après publication
et vérification de son pin ; une collision ou un checkpoint contradictoire
échoue sans suppression/réattribution de fichiers du destinataire.

Un fichier modifié par B après un envoi achevé est conservé : une nouvelle
action explicite crée une autre copie, sans écrasement. Une suppression chez
B ne déclenche aucune restauration depuis le journal ; scan et Sync ne
consomment pas ces checkpoints. Une nouvelle action Envoyer reste possible.

`tests/test_local_transfer_precommit_recovery.py` couvre des arrêts de vrais
processus avant le lien final, avant la transaction et pendant l'insertion
du deuxième OriginLink, ainsi que rollback SQL, remplacement d'inode,
symlinks, altération d'octets/checkpoint et disparition de l'Artifact source.
Les copies interrompues avant l'enregistrement d'un checkpoint achevé restent
des temporaires non publiés : elles ne sont ni adoptées ni promues par nom/SHA.
