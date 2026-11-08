# Core P0 #4858 W2 — Veto des manifestes Kobo de portée contradictoire

## Risque constaté

Le fournisseur livre des entrées de manifeste brut et l'ancienne voie W2
compose systématiquement `entitlement_type=owned` depuis la session
courante, avant la première acquisition. Une entrée ayant
`account_id` différent, un credential/provider contradictoire ou une
marque de prêt pourrait alors produire un **droit d'achat fictif** via
le miroir 0.7 W1, même sans GET et sans preuve A→B.

## Protection

Avant `_refresh_manifest_record` / `backup_put`, W2 rejette et compte
comme manifeste non fiable :

- `provider_id`, `credential_id` ou `account_id` explicitement
  contradictoires à la session épinglée ; le primaire Kobo
  `account_id=""` est accepté **seulement** si c'est la valeur exacte
  de la session valide ;
- les alias `product_id` et `id` fournis ensemble et contradictoires ;
- `loan_id` ou `expires_at` renseignés, ou un `entitlement_type`
  explicite différent de `owned`.

Le fournisseur Vault 2.x peut omettre ces attributs facultatifs. Cette
omission conserve le comportement de compatibilité antérieur ; elle ne
constitue **pas** une preuve de droits canoniques. Les revendications
contradictoires n'écrivent ni `backup_items` ni ses projections, ne
changent pas d'archive antérieure, et ne déclenchent aucun GET.

Tests : portée étrangère, faux prêt, identifiants d'édition divergents,
archive préexistante immuable, livraison d'un achat strictement cohérent.

Cette tranche ne remplace pas le cutover A→B : #4858 et #3196 restent
ouverts jusqu'à disparition de l'autorité positive du writer W1/W2.
