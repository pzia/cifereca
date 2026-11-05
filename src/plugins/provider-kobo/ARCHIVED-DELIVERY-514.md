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
