# P0 #2565 — diagnostiquer les limites de réconciliation, sans exfiltration

## Preuve terrain du 21 octobre 2026

Deux essais d'analyse locale ont été **incomplets** ; au deuxième :
320 fichiers observés et 320 SHA calculés, 120 liens de provenance
confirmés, 200 fichiers sans lien confirmé, 408 droits sans fichier
attesté, 273 chemins persistés absents. Les catégories **se recoupent**.
L'inventaire s'est arrêté sur son plafond par défaut de **120 s**,
bien avant le plafond de 4096 fichiers. Le snapshot cohérent SQLite/WAL
était **terminé**, autour de 2,6 s : pas de raison de rouvrir le problème
WAL ni de conclure que 408 livres seraient manquants.

Le journal diagnostique a aussi montré :
`epub_parser_limit_or_invalid`, `structure_limit_or_invalid`,
`metadata_candidate_budget_or_cancelled` et `time_budget`.
L'ancien code amalgamait timeout, annulation et bornes de références
dans ce troisième code ; aucun gain de performance n'était mesuré.

## Sous-lot instrumentation #2565

L'inventaire **lecture seule** publie désormais dans
`Inventory.redacted()` quatre paires **fixes** d'entiers, uniquement
agrégées au niveau de la session :

- `phase_ms.sha256` / `phase_calls.sha256` : lecture et hash ;
- `phase_ms.epub_opf` / `phase_calls.epub_opf` : interprétation
  métadonnées EPUB OPF ;
- `phase_ms.structure` / `phase_calls.structure` : vérification
  structure / CRC bornée ; un PDF n'est jamais annoncé comme vérifié
  à fond à partir de sa simple signature ;
- `phase_ms.pdf_signature` / `phase_calls.pdf_signature` : contrôle
  limité de signature PDF.

Les durées de phase sont d'abord **sommées en nanosecondes**, puis
arrondies à la milliseconde. Un compteur indépendant
`files_started` distingue un fichier ouvert puis interrompu pendant
le SHA d'une observation terminée. Il ne mesure **pas** les EPUB
entièrement valides ; consulter `validation` et
`structure_validation` séparément. Les temps sont des **durées
cumulées par étape**, non un benchmark p95/RSS et non le temps WAL.

La recherche des candidats bibliographiques expose seulement
`catalog_rows_examined`, `references_examined`, `files_examined`,
`index_ms` et `match_ms`. Les codes sont **désambiguïsés** :

| Code | Sens |
| --- | --- |
| `metadata_candidate_time_budget` | Plafond de 10 s de recherche atteint (par défaut) |
| `metadata_candidate_cancelled` | Signal d'interruption reçu, y compris budget global |
| `metadata_candidate_row_budget` | Plus de 100 000 notices indexables |
| `metadata_candidate_reference_budget` | Plus de 100 000 identifiants examinés |
| `metadata_candidate_keys_budget` | Au moins une notice dépasse 64 identifiants |
| `metadata_candidate_count_budget` | Plus de candidats que le plafond autorisé |

Le préfixe ancien `metadata_candidate_budget_or_cancelled` reste
accepté à l'affichage pour compatibilité avec les anciens résultats,
mais **n'est plus émis** par la phase actuelle. L'affichage Maintenance
est compatible avec un Core plus ancien sans aucune métrique : il omet
alors seulement les nouvelles lignes. Les nombres sont validés par
une liste fermée de clés et de types. Les valeurs inconnues, URL
signées, chemins, identités, titres, ISBN, SHA et exceptions ne sont
jamais recopiés dans ces lignes.

## Garanties et recette

Les fonctions continuent de travailler à partir des **mêmes racines,
FD vérifiés, limites SHA, anti ZIP-bomb/XXE, CRC, contrôles de
fichier renommé, barrières identité/SQLite**. Ni cache persistant de
SHA/CRC/OPF ni écriture d'artefact, de log privé, de couverture, de
placement, de droit, ni appel réseau n'a été ajouté.

Les tests déterministes vérifient la non-écriture, le secret absent
des projections, les quatre durées bornées, la conservation d'un fichier
commencé mais non vérifié, un corpus de petites archives, le plafonnement
des notices/identifiants/candidats et les deux fenêtres où le timeout
peut survenir (index ou fichiers). Le volume réel doit ensuite être
réexécuté pour identifier quelle étape consomme les 120 s :
**aucun accélérateur, aucune hausse à 600 s, aucun abandon de CRC**
avant mesure. Les deux tickets P0 #2565/#2657 restent ouverts ;
aucun rattachement Numilog/Kobo ne découle de ces statistiques.
