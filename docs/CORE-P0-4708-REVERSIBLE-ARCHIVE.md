# #4708 — Proposition d’archivage réversible d’un Placement missing

Statut : contrat préparatoire, aucune activation ni suppression autorisée.
Les 282 absences historiques observées ne constituent pas une liste
de candidats supprimables. Le diagnostic / la proposition GTK restent
des observations, jamais des jetons de retrait.

## Témoin privé

Le codec v1 conserve les onze colonnes SQLite d’origine (nullabilité,
identité, chemin exact, état missing, dates et chaîne metadata_json intacte).
Enveloppe canonique ASCII, SHA-256 d’intégrité, limite proposée 1 MiB par
enveloppe ; aucun champ inconnu ni JSON ambigu admis. Le digest n’est
pas une signature, un consentement ou une preuve de dépendances.
Ne pas exporter les octets, identifiants ou chemins. Un futur writer
doit vérifier le schéma live exact avant d’extraire une ligne : une
colonne nouvelle ne doit pas être perdue par l’archive v1.

## Transition envisagée, pas implémentée

- Nouvelle inspection complète, sélection exacte, garde Core de
  dépendances historiques, absence physique et racine nofollow.
  Activer explicitement maintenance.destructive_cleanup_enabled (défaut
  false) et recueillir une confirmation spécifique, non réutilisable.
- Sous verrou d’acquisition et BEGIN IMMEDIATE/FULL, vérifier l’époque
  de configuration et le témoin courant ; insérer l’archive privée
  entière et retirer exclusivement la ligne Placement exacte dans la
  même transaction. Interdire collision d’identifiant, chemin, archive,
  schéma ou dépendance. Aucun DELETE de fichier, Artifact, OriginLink,
  SourceReference, Entitlement ou AcquisitionLedger.
- Postcheck durable, erreur de commit ambiguë à réconcilier sans second
  retrait. Restauration supervisée exacte, état missing conservé ;
  si le chemin est occupé, préserver et proposer une autre revue.
  Aucun GET, relocalisation inférée, nouvelle origine ou droit.
- Fixer capacité archive et durée de conservation avec le propriétaire ;
  saturation = refus fermé, aucune purge. Préserver les archives dans
  le cycle de sauvegarde et de restauration de la base.

Le codec ne fournit aucune commande d’écriture et ne modifie pas le
schéma SQLite. Les retraits historiques #2847 ne deviennent pas
réversibles du seul fait de cette tranche. Avant activation, tests
de transactions injectées, reprise, collisions, changements de scope,
révocation et conservation des dépendances avec données synthétiques.
