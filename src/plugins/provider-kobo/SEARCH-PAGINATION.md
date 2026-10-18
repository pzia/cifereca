# Kobo-Vault — pagination de recherche (#1917)

La session Kobo déclare `pagination=page`, `page_size=50`,
`max_pages=101` et accepte uniquement un index Python entier non
booléen `0..100`. Toute autre valeur provoque
`ProviderCapabilityError('Kobo-Vault : invalid_page')` **avant** de
contacter les routes `/catalog/search` ou `/library/search`.

Cette vérification est essentielle pour ne pas soumettre de demande
malformée au service de bibliothèque personnelle lorsque la session
est authentifiée. Elle intervient après validation du champ/du scope
et avant résolution de l'autorité du compte. Les recherches `catalog`
restent exclusivement publiques et ne peuvent attribuer ni `owned`
ni statut de bibliothèque. Les avertissements de refus privés sont
réinitialisés à chaque tentative rejetée.

Tests `httpx.MockTransport` offline : 27 combinaisons page/scope
invalides, trois pages publiques limites valides. Aucun changement de
droit, d'acquisition ou de route HTTP ; le plafond local n'atteste pas
l'existence d'autant de pages sur l'API distante.
