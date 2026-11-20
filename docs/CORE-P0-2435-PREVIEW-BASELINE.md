# #2435 — baseline previews avec concurrence bornée

Commande : `PYTHONPATH=src pytest -s -q tests/test_core_p0_2435_preview_baseline.py`.
16 et 256 JPEG synthétiques 600×900, dérivés 180×270, cache média préchauffé,
2 workers de preview existants, séries de 1 ou 8 requêtes en vol.
Chaque série a une phase dérivés absents puis présents ; seuls les dérivés
temporaires sont supprimés. Zéro GET, zéro fallback, mêmes dimensions et
fichiers source conservés. Aucun changement de pool ou priorité.

Le rapport distingue GdkPixbuf (si disponible) du backend Pillow adapté
au contrat de conversion ; les mesures Pillow ne valent pas mesure GTK.
P50/P95 du callback (queue comprise), CPU, RSS maximal cumulatif, octets
fichiers et compteurs Linux /proc/self/io (si disponibles). Ces compteurs
mesurent l'activité du processus et les octets imputés par le noyau ;
pas les E/S physiques du périphérique ni un OS cache froid. Les tests
vérifient l'invalidation et les compteurs, pas de SLA temporel.
Comparer les tailles et séries sur le même backend, pas entre runners
ou implémentations. Choisir une optimisation seulement sur gain attesté.
