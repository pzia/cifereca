# Réconciliation de bibliothèque — première tranche Core (#2565)

Cette primitive hors réseau inventorie les EPUB/PDF de racines explicitement
autorisées par identité, lit les métadonnées OPF sans couverture/cache et croise
les preuves de fichiers avec un snapshot SQLite. Elle ne répare aucun lien,
ne lance aucune acquisition et ne démarre ni `LibraryService` ni `Storage`.

## Utilisation autonome

Fermer Cifereca normalement, puis fournir les chemins et identifiants exacts
locaux ; les valeurs ci-dessous sont des exemples synthétiques. Ne pas copier
les commandes réelles ou le rapport privé vers les journaux, LLM ou CI.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m cifereca.core.library_reconciliation \
  --database /chemin/vers/cifereca.sqlite \
  --root identite-exemple=/chemin/vers/bibliotheque/identite-exemple
```

Répéter `--root` pour des racines disjointes autorisées. Aucun répertoire n'est
créé ou découvert automatiquement. La sortie standard est un JSON expurgé :
compteurs et causes fermées, sans chemins, titres, comptes ni SHA. Code retour
`0` pour une analyse complète, `2` pour limites/indisponibilité. Ctrl+C interrompt
la lecture entre entrées ou blocs SHA ; le rapport conserve l'incomplétude.

Pour consulter les preuves complètes **localement**, ajouter explicitement
`--private-report /chemin/local/nouveau-rapport.json`. Cette option est la seule
écriture volontaire : fichier nouveau en mode 0600, jamais de remplacement.
Il contient des données privées (chemins, titres, auteurs, identités, comptes,
SHA et lignes de preuve). Ne pas le joindre à un ticket ou l'exporter vers un LLM.
Le rapport est un constat daté implicitement par son exécution, jamais un jeton
permettant une réparation ultérieure.

## Contrats et limites visibles

- `scan_identity_roots()` reçoit des capacités de racines validées par l'appelant.
  Il refuse chevauchements, symlinks dans les ancêtres/racines/entrées et fichiers
  spéciaux. Limites globales : entrées, fichiers, octets/fichier, octets SHA,
  profondeur, durée, métadonnées privées cumulées (64 Mio au maximum) ; progression expurgée et annulation injectable.
- SHA, OPF et structure/CRC sont lus sur le même descripteur ; inode, taille et dates de
  modification sont revérifiés après lecture, ainsi que le nom courant du fichier.
  Le résultat n'est pas une garantie qu'un livre n'a pas changé ensuite.
- `inspect_epub(..., metadata_only=True)` conserve l'extraction OPF existante,
  sans lire les images ni appeler le cache. Le répertoire central est borné
  **avant** construction de `ZipFile`. ZIP64/multivolume non pris en charge,
  limites ZIP/XML et rejet DTD/entités, y compris UTF-16/32.
- EPUB `ok` signifie métadonnées OPF lisibles. Le champ distinct
  `structure_validation=epub_structure_crc_ok` exige en plus les règles
  structurelles du validateur Core (mimetype, conteneur, racines/package, doublons,
  membres ZIP non chiffrés) et les CRC de tous les membres. L'adaptateur borne
  entrées, octets décompressés cumulés (par fichier et par audit), ratio et durée ;
  chaque bloc est annulable. Aucune couverture n'est extraite ou mise en cache.
  Le graphe ne confirme un EPUB qu'avec ces deux contrôles réussis. Cela ne
  remplace pas EPUBCheck ou une validation de rendu/spine/accessibilité.
- PDF `pdf_structure_minimal` exige signature `%PDF-` et marqueur `%%EOF`,
  **pas** conformité PDF ni preuve DRM. Le graphe ne confirme pas un PDF sur
  cette seule base. Les résultats structurels incomplets/invalides restent visibles.
- `read_quiescent_snapshot()` copie au plus 128 Mio d'une base stable sans
  journal actif puis interroge **en mémoire** les six tables de bibliothèque et les deux tables de
  rattachements ADEPT locaux. Il ne crée
  pas de SHM/WAL, ne fait aucune migration, récupération ou checkpoint. Une base
  avec `-wal` ou `-journal` est refusée (`snapshot_live_journal`) plutôt que lue
  sans ses transactions. Une fermeture normale suffit généralement ; **ne pas
  supprimer manuellement les journaux**. Aucun contournement automatique.
- `confirmed_link` exige un droit actif d'achat attesté par le manifeste personnel
  Kobo ou la bibliothèque authentifiée Numilog, le même compte/credential/identité/
  achat, un unique ledger `complete`, un placement explicite et un artefact
  `sha256:` de même SHA/taille, et structure/CRC EPUB validés. Aucun droit n'est attribué par nom, ISBN ou SHA.
  Les snapshots incomplets ne produisent pas de confirmation.
- `present_unlinked` est une correspondance d'octets avec un artefact ou une
  sauvegarde de la même identité, sans attribution de droits. Les liens refusés
  restent `local_file_without_attested_right`/`ambiguous` ; les divergences sont
  `hash_mismatch`. Un droit non vérifié n'est jamais appelé livre absent.
- Une passe `inspect_stored_paths()` distincte vérifie les chemins explicites
  des placements dans les seules racines autorisées. `stored_path_missing`
  exige ENOENT, deux observations concordantes et les mêmes ancêtres/racine.
  Permissions, symlinks, fichiers spéciaux, racine absente ou remplacée restent
  `stored_path_unavailable` ; les chemins étrangers sont `stored_path_out_of_scope`.
  Limites de durée/chemins et annulation, sans ouverture du contenu des livres.
- `metadata_candidates()` recherche uniquement des identifiants explicites
  scheme/valeur dans les notices de **la même identité**. Les ISBN 10/13 exigent
  une clé de contrôle valide, sans recherche approximative ni déduction par
  titre/auteur/nom. Plusieurs notices concordantes restent ambiguës. La certitude
  est toujours `metadata_only`, jamais un droit ou une autorisation de réparation.
  Le snapshot ne conserve que ces identifiants bibliographiques des métadonnées
  catalogue (pas les blobs JSON, jetons ou URL). Budgets de références/candidats
  et annulation sont visibles.
- La CLI restreint les lignes privées aux identités explicitement sélectionnées.
  Les acquisitions contradictoires du même droit/compte restent conservées pour
  ne pas transformer un conflit en confirmation. Les artefacts globaux, dépourvus
  de compte ou de titre, servent seulement à comparer les octets.
- Les compteurs par identité/fournisseur utilisent des indices d'identité
  éphémères ; leur correspondance est uniquement dans le rapport privé local.
  Un fichier sans lien confirmé reste `unattributed`, jamais Kobo/Numilog par
  simple SHA, ISBN ou dossier. Les libellés fournisseurs exportés sont fermés.
- Les références ADEPT lues sont les seuls rattachements persistés identité/profil
  et emprunteur Paris/profil. `local_reference_only` ne prouve ni profil utilisable,
  ni prêt actif, ni droit. Aucune licence/certificat/ACSM n'est lu et aucune CLI
  ADEPT ou source fournisseur n'est exécutée.
- Les relations sont des indices éphémères de fichiers et de lignes, avec raison
  et certitude (`bytes`/`attested`). Les sources ADEPT ne sont pas interrogées :
  `adept_remote_inventory=not_requested`. Aucun fichier de profil/licence/jeton
  n'est lu ; seuls les identifiants de rattachement SQLite sont conservés en privé.

## Suite de #2565 (non livrée par cette tranche)

Snapshot sûr pendant une session avec WAL actif, rapprochements par édition au-delà
des identifiants explicitement attestés,
liens catalog_entries/backup plus riches,
inventaire ADEPT distant consenti, API de service et
entrée Maintenance asynchrone avec filtres/inspection. L'absence de chemin dans
cet inventaire ne suffit jamais à conclure `stored_path_missing` : seule la
passe de contrôle explicite ci-dessus peut produire ce constat.

Les réparations sont un lot distinct : jeton de confirmation, preuve fraîche,
transactions et annulation. Ne jamais convertir ce rapport en autorisation de
rattachement ; le protocole Kobo existant reste la seule voie autorisée pour les
cas qu'il sait attester. Les lots UI et réparation n'ont pas de recette attestée
par cette tranche. L'issue #2565 reste ouverte.
