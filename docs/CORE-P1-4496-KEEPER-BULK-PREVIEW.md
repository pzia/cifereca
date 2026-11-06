# P1 #4496 — « Tout accepter » au niveau des recommandations keeper

La politique de classement/dédoublonnage Q4 du 05/11/2026 permet à
l'utilisateur de **pré-sélectionner en bloc uniquement les recommandations
individuellement sûres**, après inspection ; elle ne réactive pas la
suppression ordinaire (P0 #3185 : gel toujours en place).

Le Core fournit `preview_keeper_bulk(reviews, identity_id, accept_all)` qui
compose **en lecture seule** les rapports `review_canonical_keeper`
(une observation physique/dépendances déjà effectuée par artefact).
L'API `KeeperBulkPreview` retourne trois listes explicites :

- `eligible` : les choix non ambigus (keeper + copies candidates), bornés ;
- `preselected` : sous-ensemble identique à `eligible` seulement lorsque
  `accept_all=True`, sinon vide ;
- `blocked` : les cas incertains refusés, conservant leur raison de refus.

Le Core refuse le mélange de profils, les artefacts ou placements dupliqués,
les listes de retraits incohérentes, les décisions forgées `executable=True`,
les candidats sans keeper et la sélection implicite par l'ordre d'entrée.
Les références retournées restent un aperçu **périssable** ;
`executable=False` dans toutes les situations.

Les exclusions Outbox et les contrôles de références/FS/SHA appartiennent
toujours au reviewer unitaire. Un lot `accept_all` ne confère **aucun
consentement**, aucune revalidation du snapshot, aucun verrou, aucune
transaction SQLite, aucun `unlink`, aucune opération de recovery.

## À intégrer ultérieurement dans #3176

L'interface peut afficher le nombre de choix préselectionnés et de cas
bloqués, demander une confirmation finale explicite, puis soumettre des
**opérations individuelles** à un superviseur Core qui revalide frais
identité, SHA, dependencies, exclusions et génération des placements sous
locks exclusifs. Ne jamais passer cette prévisualisation directement à
`canonical_dedup_supervisor` ; le gel de suppression ordinaire doit rester
effectif sans autorisation explicite de réactivation.

Tests headless : sélection partielle, blocs, profil, ordre, collisions et
limites ; aucune donnée privée ni fichier réel n'est requis.
