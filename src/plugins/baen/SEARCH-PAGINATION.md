# Baen Free Library — pagination de recherche (#1917)

La recherche de catalogue public déclare `pagination=page`,
`page_size=None` (taille uniforme **non attestée**) et `max_pages=101`.
La catégorie JSON observée utilise `pageSize=20` ; la recherche textuelle
historique non qualifiée **demande** `pageSize=50`. Ce paramètre n'est pas
une preuve que le serveur le respecte. Seuls les index Python entiers
**0 à 100** sont acceptés. La page zéro correspond à `page=1` envoyé au fournisseur,
la page 100 à `page=101`. L'exactitude du découpage, de la recherche et
de l'exhaustivité serveur reste **inconnue**, même sur HTTP 200.

Une page négative, un booléen, une chaîne, une fraction, `None` ou
un index au-delà de 100 déclenche `ProviderCapabilityError('Baen : invalid_page')`
**avant tout appel HTTP**. Il n'y a plus de conversion silencieuse des
pages négatives vers la première page, ni d'erreur Python brute de conversion.

Les exemples de test sont synthétiques (pas de validation live des
endpoints Baen) ; cette correction n'étend pas la recherche au-delà du
champ `all` ni ne modifie l'acquisition. La mise à jour du site Baen vers
une route Magento reste à qualifier séparément dans la PR brouillon #1057.
