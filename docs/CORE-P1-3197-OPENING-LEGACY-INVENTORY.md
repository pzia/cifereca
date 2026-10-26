# Core P1 #3197 — couverture des prêts à l'ouverture (lecteurs v1)

Le parcours `opening_sync` peut lire un producteur **typé** par
`loan_inventory_snapshot(account)` ou un fournisseur installable historique
par `_loans_for_account(account, force=True)`.

L'ancien canal mutable `session.last_loan_errors` ne peut pas certifier
la couverture DILICOM/SYRACUSE/BnF, même lorsqu'il est absent ou vide :
une autre lecture ou un autre emprunteur peut l'avoir remplacé. **Le Core
ne le consulte plus pour décider si l'ouverture des prêts est complète.**

Pour une session qui annonce `capabilities.loans=True` mais ne publie pas
de snapshot typé, **chaque lecture v1 représente un inventaire
`loan_legacy_unverified`** et l'état de la source est `partial`.
Un seul **diagnostic** de couverture non vérifiée est enregistré par
compte, sans calcul fondé sur la longueur des erreurs fournisseur ; cette
incertitude n'incrémente **pas le compteur d'erreurs réseau/acquisition**.
L'état de source et celui de l'identité restent néanmoins `partial`. Les prêts
positifs sont toujours acceptés sous les validations de portée existantes et
ne sont jamais retirés à cause d'une absence dans la liste. Kobo, Numilog et
les autres fournisseurs qui n'annoncent pas de capacité `loans` ne sont pas
déclassés artificiellement par ce changement.

Une lecture typée `success` ne peut servir de preuve négative que via les
branches `digital` exhaustives attestées, et sous revalidation des liens
identity/session/epoch. **Aucune nouvelle acquisition, restitution ou
suppression de fichier** n'est autorisée ici.

L'API plugin v1, les caches du tableau de bord et les retours PNB restent
compatibles pour cette tranche. L'intégration des autres consommateurs sous
le contrat d'inventaire et une dépréciation versionnée de v1 restent les
étapes ouvertes de #3197. Tests SQLite et sessions synthétiques seulement,
sans recette live ni release.
