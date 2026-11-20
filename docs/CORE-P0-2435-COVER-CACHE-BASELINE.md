# #2435 — baseline reproductible N1/N2/N3 URL

Commande : `PYTHONPATH=src pytest -s -q tests/test_core_p0_2435_cover_cache_baseline.py`.
16 et 256 documents fictifs, 2 workers existants, client HTTPX MockTransport, cache scratch isolé.
Quatre phases : N2/N3 froids, chauds, N2 chaud média retiré, révision documentaire nouvelle média chaud.
Le test impose les deltas N1/N2, GET, HIT média et octets publiés ; n'impose aucun SLA de vitesse.
Sortie JSON COVER_CACHE_BASELINE : p50/p95/max du délai appel→callback (inclut attente et accès média),
CPU process, RSS high-water cumulatif Linux, octets logiques écrits, taille des fichiers et compteurs récents
existants par étage (fenêtres cumulées dans le run, pas de percentile de phase pour ces compteurs).
Pas de mesure de disque froid/physical I/O, pas d'EPUB, de preview GdkPixbuf ni de rendu GTK N4 dans cette
première tranche ; ne pas assimiler JPEG/PNG signature validée à un affichage. Aucune mesure terrain
ni optimisation avant comparaison de mesures reproductibles. Aucun changement de priorité N1 ou budget.


## Extension N3 EPUB

Même commande et tailles. Chaque archive synthétique a une image PNG décodable distincte et 64 Kio
de données inertes non compressées. La phase froide supprime les médias extraits lors de la construction
de la preuve, puis mesure le vrai lecteur de restauration (SHA intégral, ZIP, membre exact et publication).
La phase chaude valide le média adressé sans rouvrir l'EPUB. Assertions : 1 restauration par source froide,
0 nouvelle restauration chaude, aucun échec/overflow, mêmes octets. Les octets logiques hachés sont calculés
depuis le corpus connu et les restaurations attestées ; ils ne sont pas des octets de disque mesurés.
La sortie des deux benchmarks est aussi imprimée hors capture pytest pour réception CI ciblée.
N4/preview réels, petite/grande bibliothèque réelle, parallélisme ou gain après optimisation restent à recevoir.
