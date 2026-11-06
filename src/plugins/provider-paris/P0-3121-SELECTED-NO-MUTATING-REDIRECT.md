# P0 #3121 — frontière HTTP de livraison Paris sur prêt déjà acquis

La livraison canonique d'un prêt Paris **déjà observé** sélectionne un
`EbookUrl` DILICOM et peut suivre des redirections HTTP. Un redirect vers
`ASSARedirect.ashx` ou `DownloadDigitalDocument` peut réévaluer le
prêt/quota, et ne doit jamais servir de second chemin d'acquisition
lorsqu'un premier ACSM a été obtenu pendant l'emprunt manuel.

Dans `download_digital(...,expected_right_id=...)`, le plugin garde une
barrière `selected_offer_only` avant **chaque** HTTP GET, y compris
après 302. Le chemin décodé avec `unquote` est refusé s'il désigne
`ASSARedirect` ou `DownloadDigitalDocument`, sans nouvelle requête.
Les téléchargements historiques explicitement demandés restent inchangés.
L'absence de nouvelle émission du claim d'emprunt ne suffit pas :
ce garde couvre les réémissions mutatrices en dehors de Core.

Les tests `test_p0_3121_selected_no_mutating_redirect.py` vérifient
le refus avant le premier GET, l'encodage du chemin, et le refus d'un
redirect après un premier GET de jeton. Aucune requête réelle.
