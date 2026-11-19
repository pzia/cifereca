# #4520 / #4496 — Reprise et rétention des variantes dérivées

## Contrat conservatoire et frontière d'activation

Référence de départ : main edb05e2acfa272ae4452b05f2c60eb6f048c9704.
La reprise d'un lien **déjà matérialisé** n'est ni une transformation, ni
une acquisition, ni une suppression. La primitive privée Core est testable
sur une bibliothèque synthétique ; sa présence n'est pas une autorisation
de l'exposer en production. Aucun appel au writer depuis Sync, startup,
plugin ou simple affichage Maintenance. La confirmation à blanc (#6858,
à réceptionner séparément) n'est pas un consentement à une écriture réelle.

La préférence Core #4496 (défaut false) est indépendante de la reprise.
Ni ce document ni une recette réussie n'autorise la rétention destructive.

## Comptabilité et rétention

| Deux mesures distinctes | Portée | Limite |
|---|---|---|
| Réservations journal v2 | DB entière, pending et committed | 1 024 reçus, 8 192 octets par reçu, 8 MiB de payload cumulé, 16 GiB de tailles logiques déclarées cumulées |
| Stock staging référencé | Profil exact, noms attestés par les reçus, inodes distincts | Observation bornée coopérativement, sans inventaire global ni preuve SHA |

Le passage pending → committed ne libère aucun quota : stockage des reçus
et budget de tailles logiques restent comptés. Le hardlink staging/cible
ne compte qu'une fois dans la mesure observée par inode. Les blocs
alloués ne prouvent pas des octets réellement récupérables (liens,
reflinks, fichiers ouverts, partage d'extents, etc.). Une absence de
stage ne prouve ni la suppression de la cible, ni le droit d'oublier
le reçu, ni l'exhaustivité physique. Un répertoire indisponible ou des
preuves changées ne sont pas des absences.

À saturation ou corruption : refus fermé avant nouveau lien ou écrasement,
pas de recyclage silencieux d'un reçu, pas de remboursement au passage
committed, pas de purge sur compteur, âge, nom ou SHA. Les fichiers
et preuves déjà présents demeurent protégés. Le quota journal ne borne pas
tout l'espace de staging : les entrées non référencées et les allocations
antérieures à la réservation restent hors preuve du rapport. La
disponibilité à long terme après saturation n'est donc **pas résolue**.

Politique de rétention durable proposée pour la suite, et non
implémentation acquise : distinguer le témoin d'audit immuable de la
réservation opérationnelle ; démontrer le lien exact avec Artifact,
Placement, OriginLink et dépendances (undo/recovery, sauvegardes, autres
liens), une transition transactionnelle et réversible, la résistance
ABA/concurrence et un postcheck sur le même scope. La libération
d'espace physique demande une preuve séparée et une autorisation
propriétaire distincte. Aucun TTL, seuil de purge ou effacement
automatique n'est décidé ici. Ne pas contourner les plafonds par
augmentation implicite.

## Matrice de recette

- Reçu pending, lien exact déjà présent : liste privée → sélection exacte →
  revue physique → consentement one-shot (fixture uniquement) → acquisition
  du verrou existant sans création/troncature → commit SQLite FULL →
  Artifact distinct, Placement du profil, OriginLink derived sans
  droit/source fournisseur nouveau, transition du reçu.
- Le résultat committed ne crée aucun hardlink, ne supprime aucun fichier,
  et les observations et réservations restent comptées. La file pending
  n'expose plus le reçu terminé ; un ancien objet de revue ne se rejoue pas.
- Lien absent, identité/époque changée, verrou absent ou collision :
  pas d'écriture ; une nouvelle revue est nécessaire. Un échec après
  transition SQL doit conserver la réservation et l'état canonique
  antérieur si la transaction a effectivement rollback ; ne pas généraliser
  ce résultat à une erreur de commit ambiguë.
- Après réouverture DB : mêmes preuves persistées et absence de reprise
  automatique. Les identifiants et chemins privés restent hors rapports
  agrégés. Pas de requête fournisseur ni de recette destructive réelle.

Les tests d'intégration de cette tranche n'activent pas de route UI
vers le writer. Réception requise sur HEAD exact. Une activation
effective, une politique de compaction/rétention exécutable, et une preuve
de capacité physique globale restent des jalons distincts. Ne pas
clôturer #4520 en présentant ces limites comme livrées.

## Préflight canonique SQL (nouvelle tranche)

La revue agrégée des reçus committed réutilise le contrôle de preuve SQL du
writer : parent et placement de résultat exacts, Artifact, recette et
OriginLink derived sans SourceReference ; elle bloque les coordonnées
staging/cible partagées avec un autre reçu (y compris pending) ou un
Placement enregistré au nom de staging. Le résultat reste une observation
SQL sur snapshot, pas une preuve physique, un inventaire exhaustif de
dépendances, une demande de compaction ou une autorisation de purge. Les
reçus non référencés ou historiques ne sont ni oubliés ni réattribués.


## Convergence et références externes au staging

La revue convergée associe les observations SQL et physiques du même
reçu et recontrôle les verdicts et tous les digests du journal. Elle
ne classe que les quatre premiers committed du profil, avec un plafond
de 1 GiB de hash et un délai coopératif. Avant hash, les noms de staging
éligibles sont aussi confrontés aux chemins explicites (placements,
backups, device placements) et aux enveloppes JSON des tables Core
catalogue, acquisition, variantes, artefacts, placements, droits,
provenance, sources, appareils et événements, ainsi qu'à metadata.
Seul le reçu exact propre à chaque nom est exempté ; les autres
références, y compris inter-profils, restent bloquantes. Budget par
snapshot : 32 768 lignes, 16 MiB de payload, maximum 1 MiB par ligne et
profondeur JSON 24 ; un dépassement ou une enveloppe indéterminée
n'est jamais classé comme absence de dépendance. Le rapport ne révèle
aucun nom ni chemin et n'affirme toujours pas une preuve exhaustive :
les alias, systèmes externes, plugins et usages hors SQL restent à
qualifier. La cible publiée demeure protégée, même si le stage est
absent. Pas de writer de rétention ni remboursement de quota.

Les clés de metadata sont également inspectées (texte et JSON encodé) ; leurs octets sont inclus dans les limites de matérialisation et de scan. Les alias non représentés dans ces valeurs restent hors preuve.
