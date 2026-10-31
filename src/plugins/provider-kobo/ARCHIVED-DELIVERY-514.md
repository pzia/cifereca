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
Une recette fournisseur réelle et les contrôles CI/install sur cette intégration
restent requis avant de fermer #514.
Les tests Plugins sont synthétiques, sans accès à un compte réel.
