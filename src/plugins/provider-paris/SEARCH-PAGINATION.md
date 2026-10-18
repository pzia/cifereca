# Pagination de recherche Syracuse — P1 #1917

Les recherches publiques `all`/`digital`/`physical` déclarent
`page_size=50`, `pagination=page` et `max_pages=101`.

La session du provider exige un index Python **entier non booléen**, borné
à `0..100`. Un index négatif, booléen, fractionnaire, textuel ou hors
borne renvoie `ProviderCapabilityError('Bibliothèques de Paris : invalid_page')`
**avant** l'appel à `ParisClient.search` : aucun retry Syracuse, aucun
changement de profil ou de compte, aucun traitement d'acquisition.
Les index valides sont transférés inchangés au client historique.

Les tests paramétrés utilisent un faux client déterministe sans compte
ni réseau et contrôlent aussi la remise à zéro des avertissements
partiels entre deux recherches. Les plafonds sont des limites locales
de prudence, pas une attestation de 101 pages réellement accessibles
sur les serveurs Paris. La PR brouillon #1077 porte séparément les
facettes bibliographiques, sans qualification JSON amont.
