# P0 #4858 W2 — collision inter-lignes des alias d’édition Kobo

L’admission W2 exige maintenant des identifiants d’édition bruts exacts
(PR #5010). Toutefois, détecter les doublons uniquement sur le
`product_id` principal laisse une fenêtre : un élément malformé avec
`product_id="other"` et `id="book-1"` est refusé individuellement,
mais un second élément légitime `product_id="book-1"` reste acquis
et peut générer un GET et une projection `owned` W1. L’édition annoncée
est pourtant ambiguë dans le manifeste complet.

La boucle préanalyse **les deux alias de chaque ligne**, en dédupliquant
les alias identiques *dans la même ligne*. Toute édition revendiquée par
plusieurs lignes est mise en quarantaine sur chaque ligne impliquée,
**avant** `backup_put` et avant tout GET, indépendamment de l’ordre.
Un alias avec espaces de bord est uniquement normalisé dans l’**index
négatif de collisions** : il ne devient jamais une clé autorisée.

Les autres éditions exactes et sans conflit continuent à être acquises ;
les archives et octets existants des éditions ambiguës ne sont pas
modifiés. Les métriques `duplicate_manifest_items` et
`malformed_manifest_items` expriment cette quarantaine et empêchent un
manifeste partiellement incohérent de paraître complet.

Tests Core/SQLite/EPUB : ordre inversé, alias secondaire différent,
alias malformé avec espaces/nouvelle ligne, indépendance d’un achat sans
conflit, archive antérieure conservée, et une seule ligne aux deux
alias identiques non considérée comme doublon.

Cette tranche W2 renforce la correction des anciennes sessions ABI
incomplète ; elle ne remplace pas leur première livraison 0.7 par A→B.
#4858 et #3196 restent ouverts.
