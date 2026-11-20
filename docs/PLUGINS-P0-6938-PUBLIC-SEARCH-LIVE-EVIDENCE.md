# #6938 — Réception des preuves disponibles de recherche publique

**20 novembre 2026 — Europe/Paris.** Ce relevé exploite les preuves terrain
déjà consignées dans [#6938](https://github.com/pzia/cifereca/issues/6938)
les 20/11 à 03:43–03:51 et 06:23, et la réception déterministe de
[#1917](https://github.com/pzia/cifereca/issues/1917#issuecomment-7022224724).
Il complète les matrices historiques #1917, sans rouvrir ce ticket.
Les six sources ne sont pas toutes qualifiées sur réseau réel.

| Source | Réponse/route réellement attestée | Contrat et comparaison | Limites restantes (#6875) |
|---|---|---|---|
| BnF Catalogue | SRU DC /api/SRU index `bib.title`, Hugo, 2 résultats par page, positions 1 puis 3, 14571 total annoncé, next 5 ; témoin unique 0. Adaptateur BnfCatalogueSession : GET 200, 2 résultats, 2 PublicSearchNotice, partial=false, catalogue_complete=false ; titre 2/2, auteur 1/2, format inconnu. | Cohérent avec page bornée et réponse typée, zéro SRU explicite ; #3026 refuse XML/diagnostics invalides. Aucun auteur ou format inventé. | Autres index, champs, supports, taux de présence, exhaustivité non attestés. |
| EbooksGratuits | /index.php/feed, OpenSearch, query=Hugo : books 1, authors 3 ; branche book 1 entrée EPUB ; témoin sans correspondance feed vide. query=le : 5 branches, books 2559 annoncés, 48 entrées puis 48 via next exact, troisième non suivie. Adaptateur EbooksSession OPDS : 4 GET 200, 1 résultat EPUB avec titre/auteur ; avertissements auparavant vides. | OPDS par défaut, forme compatible COPS, pagination via next, champs spécialisés postfiltrés ; #3022/#3041 refusent panne/XML invalide. Anomalie démontrée : prédicat de branches alternatives limité au rang de sélection, donc recherche all sans avertissement malgré portée partielle. Correction dans ce lot : avertissement si plusieurs destinations sûres dans navigation d'une recherche all non vide, sans requête supplémentaire. Tests synthétiques positif/vide. | Sémantique des autres branches/champs, filtres et exhaustivité non qualifiées. Pas de média ni acquisition demandés. |
| Gutenberg | /ebooks/search.opds/?query=Hugo : HTTP503 HTML, 913 octets, reproduit à 06:23. | #3019/#3059 : indisponibilité, pas zéro Atom ; limites et next bornés. | Cause fournisseur/proxy inconnue, succès/zéro/pagination/filtres non reçus. |
| Gallica | /services/engine/search/opds HTTP403 HTML, reproduit à 06:23 ; signal terrain #1917 du 15/11. | #3021 : refus typé, résultat partiel si autre branche saine, aucun zéro global. | Cause non attribuée ; routes/champs SRU et OPDS non qualifiés. |
| Paris Syracuse | Non interrogé en l'absence de scope/session légitime exacts. | #3028 : all, branches CATALOGUE/EBOOKS ; fallback de taille seulement erreur métier, pas refus HTTP. | Qualification distante de chaque scope ; aucune session inventée. |
| Kobo | API Provider non interrogée faute de base/scope public exact. | #3029 : all, public catalog distinct bibliothèque privée, refus privé partiel ; aucun droit par repli. | API réelle, pagination/zéro/champs ; site marchand non équivalent. |

Les identifiants et SHA256 des corps publics sont dans les commentaires
[#6938, 03:43](https://github.com/pzia/cifereca/issues/6938#issuecomment-7028733010),
[03:51](https://github.com/pzia/cifereca/issues/6938#issuecomment-7028803127),
[adaptateurs réels](https://github.com/pzia/cifereca/issues/6938#issuecomment-7028901886)
et [06:23](https://github.com/pzia/cifereca/issues/6938#issuecomment-7031280233).
Les en-têtes no-cache n'attestent pas un contournement du cache distant.

**Réception déterministe distincte :** #1917 a été clos le 19/11 après
#6950 (CI 44614114901 et E2E 44614114933, transport synthétique, vrai GTK/Core).
Ce n'est pas une preuve des six réseaux. Le présent lot ne répète aucun GET,
ne réalise ni crawl, ni acquisition, ni accès privé. Aucune capacité ou droit
n'est déduit du simple catalogue. Les qualifications résiduelles sont
explicitement suivies par [#6875](https://github.com/pzia/cifereca/issues/6875),
P2+PU non bloquant, suivant l'arbitrage du 20/11. La clôture de #6938
nécessite la réception de la correction constatée au HEAD exact, pas un
PASS fictif des quatre sources indisponibles ou non interrogées.
