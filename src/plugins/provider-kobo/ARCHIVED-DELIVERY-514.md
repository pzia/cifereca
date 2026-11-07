# Achats archivés : garde de livraison (#514)

Le snapshot de droits conserve l'achat exact en état `active` avec sa preuve
fournisseur. Le booléen amont strict `archived=true` donne
`delivery=unavailable` et `delivery_unavailable_reason=archived`, sans offre.
La raison est émise uniquement lorsque le manifeste authentifié signale
`archived` comme booléen strict `True`, jamais à partir de `collection`,
`downloadable=False`, du titre ou d'une erreur réseau. Le nom de collection seul ne remplace pas
ce booléen ; une valeur mal typée n'est pas une preuve positive admissible.

La sélection d'une offre relit le manifest du compte exact puis renvoie ce
droit indisponible sans POST de descripteur. Le staging relit également le
manifest : une offre antérieure ne permet pas d'acquérir un livre archivé
depuis sa sélection. Aucun GET de contenu ni fichier temporaire n'est créé.
Un autre compte possédant le même produit conserve sa propre observation.

Cette garde ne révoque aucun achat, ne supprime aucun Placement et n'appelle
aucune restauration distante. Les snapshots restent partiels, positifs seuls.

La fiche fournisseur Kobo ne traduit pas `archived=True` en erreur
`attention_required` ni en action « restaurer » : elle conserve
`availability_status=owned` et affiche « archivé · téléchargement ignoré ».
Pour une valeur d'archive mal typée, le statut demeure non vérifiable.
Même lorsque l'archive n'est attestée qu'à la réponse HTTP `409 archived_book`,
le client Vault conserve le code/status pour le Core mais ne demande plus
de restauration ni de nouvelle tentative.

## Réception et dépendance Core

Les tests `tests/test_archived_delivery.py` couvrent deux lectures successives,
les flags de livraison contradictoires, le changement après sélection et deux
comptes distincts. Les suites existantes préservent les clôtures de session,
les gardes de propriétaire et le refus des flags mal typés.

Le Core fusionné via #3662 consomme la raison `archived` comme statut
`skipped_archived` non-erreur, compte séparément les archives, et normalise le
`409 archived_book` sans relance. Le client Vault conserve ce code fournisseur.

## Sélection tardive après manifeste non archivé (#3252)

Si le manifeste exact annonce encore un achat non archivé, mais que le **POST
sélectionné et authentifié du même compte** retourne explicitement
`HTTP 409 / error.code=archived_book`, la réponse est une observation
fournisseur plus récente de **l'indisponibilité de livraison**, sans
révocation de l'achat. Le préflight retourne la même ligne positive
`state=active`, `delivery=unavailable`,
`delivery_unavailable_reason=archived`, **sans** nouveau GET, tentative de
restauration ou inférence d'un droit. Cette réponse est obligatoirement
recontrôlée après le POST contre le compte, l'identité et la génération
de session encore épinglés ; une réaffectation annule le résultat.

Le 409 `conflict`, `revision_mismatch`, un code absent ou un autre statut
HTTP ne prouve pas une archive : le Core reste en `pending`/refus selon le
cas. Le motif est fermé et expurgé dans `EVENT kobo selected_offer` ;
le message amont et les URLs privées ne sont jamais journalisés. Une
sélection future, après restauration sur Kobo, refait son admission et peut
redevenir `available` : aucune quarantaine durable ni réécriture SQLite
n'est créée par ce chemin. Cette extension ne permet pas d'attribuer les
quatre 409 observés du grand compte sans leur vrai `error.code`.
Une recette fournisseur réelle et les contrôles CI/install sur cette intégration
restent requis avant de fermer #514.
Les tests Plugins sont synthétiques, sans accès à un compte réel.


## Livres inaccessibles chez Kobo, puis archivés (K1, 07/11/2026)

Si le lecteur constate que l'ouvrage n'est pas ouvrable **même sur Kobo**
et l'archive manuellement, Cifereca ne doit pas maintenir cette édition
comme un échec de réception une fois le **nouveau manifeste authentifié**
`archived=true` effectivement reçu. Le Core existant le classe
`skipped_archived` (pas `failed`) et ne retente aucune réception.
Le provider ajoute l'observation **informative** dans le journal privé :

```text
EVENT kobo archived_purchase product_id=... title='...' delivery=unavailable reason=manifest_archived non_failure=true
```

Le titre provient uniquement du **même** manifeste Kobo autorisé et accepté
par les gardes d'owner ; il est limité à 256 caractères et encodé avec `%r`
(aucune injection de lignes). L'événement est une observation fournisseur,
**pas** un verdict Core ni une preuve d'archivage avant relecture. Pas
d'identifiant de compte, de token ni d'URL dans cet événement.

**Important** : une erreur HTTP de livraison, à elle seule, ne suffit
jamais à marquer un ouvrage archivé ou à masquer un véritable échec.
L'utilisateur doit archiver chez Kobo ; Cifereca n'effectue aucune mutation
distante et n'infère jamais `archived` depuis un titre ou un historique
`delivery_url_invalid`. Le droit d'achat n'est pas révoqué, et une édition
déjà téléchargée conserve sa preuve locale.
