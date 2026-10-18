# Baen Free Library — pagination de recherche (#1917)

La recherche de catalogue public déclare `pagination=page`,
`page_size=50` et `max_pages=101`. Seuls les index Python entiers
**0 à 100** sont acceptés. La page zéro correspond à la page 1 du
paramètre HTTP public `page`, la page 100 à `page=101`.

Une page négative, un booléen, une chaîne, une fraction, `None` ou
un index au-delà de 100 déclenche `ProviderCapabilityError('Baen : invalid_page')`
**avant tout appel HTTP**. Il n'y a plus de conversion silencieuse des
pages négatives vers la première page, ni d'erreur Python brute de conversion.

Les exemples de test sont synthétiques (pas de validation live des
endpoints Baen) ; cette correction n'étend pas la recherche au-delà du
champ `all` ni ne modifie l'acquisition. La mise à jour du site Baen vers
une route Magento reste à qualifier séparément dans la PR brouillon #1057.
