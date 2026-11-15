# Couvertures de catalogue à preuve égale — #3680 / P1 #4979

## Contrat de résolution (lecture seule)

`resolve_cover` conserve l'ordre obligatoire **embedded > origin > catalog**,
puis `evidence_strength` décroissant. La force de preuve demeure inchangée.
À l'intérieur du tiers `catalog` seulement, deux URLs différentes de même
force sont départagées si — et seulement si — le **Core** a établi leur
appartenance à une même carte bibliographique exacte et fournit
`attested_book_identity` (clé opaque de l'Artifact ou d'un droit à scope
`identity/provider/credential/account/edition/kind` complet).

Quand une priorité de catalogue **explicite** est fournie au resolver sous
`catalog_priorities: Mapping[str, int]`, la plus grande valeur entière entre
0 et 10 000 prévaut. Les clés correspondent au champ stable `source` des
`CoverCandidate` de catalogue. Une configuration absente ou invalide est
ignorée, puis l'identifiant de catalogue, normalisé avec `casefold()`, donne
un résultat indépendant de l'ordre du réseau. Si un **même catalogue** garde
deux URLs contradictoires de rang égal, le statut reste `ambiguous` :
aucune décision arbitraire par URL.

Aucun paramètre de priorité des catalogues n'est actuellement porté par le
contrat de configuration public du Core. Le resolver accepte donc une table
optionnelle explicite mais **n'invente pas** de classement de préférences
persisté ni de règle propre à Kobo/Paris/BnF. En l'absence d'une telle table,
l'ordre stable des identifiants s'applique.

Le champ `evidence_id`, la `source` et les compteurs de `cover_resolution`
restent disponibles sans données secrètes supplémentaires. Le modèle et les
appels anciens (`resolve_cover(candidates)`) restent compatibles ; sans
preuve d'identité exacte, les URLs concurrentes restent **ambiguës**. La
comparaison de titres, d'auteurs, d'ISBN, ou des liens de prêt entre comptes ne
prouve jamais cette identité.

## Projection et affichage

`library_projection` transmet le `canonical_key` exact **uniquement** après
la jointure des droits/OriginLinks ou de l'entitlement distinct. Une carte
sous un autre credential ou emprunteur n'est pas fusionnée. Après sélection
d'une URL de catalogue, les métadonnées `catalog_cover` sauvegardées dans le
modèle sont celles de la source choisie : un refresh local ne ressuscite donc
pas une source de moindre rang.

Le rendu GTK et le `CoverLoader` reçoivent toujours `thumbnail_url` existant.
Aucun GET de catalogue supplémentaire, mutation de droits, téléchargement ni
invalidation globale du cache image n'est introduit. Les caches image restent
indexés par URL et validés indépendamment.

## Réception

- Matrice pure : source A/B même force 80, priorité explicite, égale ou absente,
  inversion de l'ordre, URI identique, source ambiguë et identité non prouvée.
- Invariants : priorité de tier et de preuve, refus d'une priorité invalide,
  absence d'agrégation de comptes différents.
- Test de projection avec vrai `LibraryService`/SQLite jetable : deux observations
  du **même reçu exact** ; URL gagnante et provenance conservées après refresh.
- Gates CI Core et GTK/XTEST sur le HEAD exact de la PR : aucune capture privée
  exportée par la recette.
