# Achats archivés : garde de livraison (#514)

Le snapshot de droits conserve l'achat exact en état `active` avec sa preuve
fournisseur. Le booléen amont strict `archived=true` donne
`delivery=unavailable`, sans offre. Le nom de collection seul ne remplace pas
ce booléen ; une valeur mal typée n'est pas une preuve positive admissible.

La sélection d'une offre relit le manifest du compte exact puis renvoie ce
droit indisponible sans POST de descripteur. Le staging relit également le
manifest : une offre antérieure ne permet pas d'acquérir un livre archivé
depuis sa sélection. Aucun GET de contenu ni fichier temporaire n'est créé.
Un autre compte possédant le même produit conserve sa propre observation.

Cette garde ne révoque aucun achat, ne supprime aucun Placement et n'appelle
aucune restauration distante. Les snapshots restent partiels, positifs seuls.

## Réception et dépendance Core

Les tests `tests/test_archived_delivery.py` couvrent deux lectures successives,
les flags de livraison contradictoires, le changement après sélection et deux
comptes distincts. Les suites existantes préservent les clôtures de session,
les gardes de propriétaire et le refus des flags mal typés.

Le bilan `skipped_archived`, son compteur dédié et la normalisation non-erreur
d'un `409 archived_book` appartiennent encore au consommateur Core. Le client
Vault conserve le code fournisseur exact ; aucun retry n'est ajouté.
**Le ticket #514 reste ouvert tant que cette tranche Core n'est pas reçue.**
Les tests Plugins sont synthétiques, sans accès à un compte réel.
