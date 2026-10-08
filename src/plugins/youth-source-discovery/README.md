# Jeunesse — Découverte de sources

Plugin de découverte pour #741.

Il reçoit des fonctions de **métarecherche** et d'**inspection de page** fournies par l'hôte. Il ne contient aucun client HTTP propre et n'active jamais une source.

## Qualification

Une source candidate n'est retenue que si une preuve d'âge explicite est trouvée dans un résultat ou une page inspectée :

- `Interest age` ;
- `Reading age` ;
- `For ages ...` / `recommended age` / `age range` ;
- `à partir de ... ans`, `jusqu'à ... ans`, `8–10 ans`.

Les mentions génériques comme `12 years old` ne comptent que lorsqu'un contexte livre/jeunesse est proche. Les âges >= 18 ans sont rejetés.

## Métarecherche

Un appel explicite lance au plus trois variantes de requête. Les résultats sont :

- normalisés en URL HTTP(S) ;
- regroupés par domaine ;
- dédupliqués ;
- classés par force et diversité des preuves.

L'inspection HTML est optionnelle et bornée à 10 pages maximum. Le plugin ne suit pas les liens découverts dans une page.

## Sécurité

Les URL avec identifiants, `localhost`, domaines `.local`, IP privées, loopback, link-local, multicast, réservées ou non spécifiées sont rejetées avant inspection.

## Sortie

`discover()` retourne uniquement une **prévisualisation** : domaine, URL d'accueil, titre, score, état `candidate` / `strong`, preuves d'âge, exemples d'URL et diagnostics. Aucun enregistrement de source ou ingestion catalogue n'est déclenché.
