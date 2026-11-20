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


## Réception CI du 20/11/2026

### Preview N3, fixture 256 JPEG, backend Pillow (PR #6962)

HEAD c60f08125cb6114b7c01b72922da70226de8e23a, run 44658503074 SUCCESS,
merge 6446a41cd2a2575a1ed187b22b0be43b5c44d596 ; 2 tests (16/256), 2.91 s.
Corpus 4 600 676 octets, dérivés 432 645 octets.

| Phase | Mur ms | p95 callback ms | CPU ms | rchar | wchar | write_bytes |
|---|---:|---:|---:|---:|---:|---:|
| Batch 1 froid | 1294.860 | 5.0003 | 1277.638 | 6697953 | 432645 | 1048576 |
| Batch 1 chaud | 34.793 | 0.1286 | 35.683 | 1481346 | 0 | 0 |
| Batch 8 froid | 688.121 | 20.3263 | 1318.160 | 6697954 | 432645 | 1048576 |
| Batch 8 chaud | 50.168 | 0.9117 | 69.886 | 1481347 | 0 | 0 |

read_bytes=0 dans les 4 phases (OS cache chaud) ; RSS high-water cumulé
68240–69424 KiB. Le batch 8 réduit le mur froid mais augmente p95 et CPU ;
à chaud, il détériore aussi le mur. Ne pas augmenter le pool ni imposer
un batch universel : aucun gain justifiant une modification de production.
Ne pas extrapoler à la latence réelle disque ou réseau.

### GTK/XTEST six vues (PR #6959)

HEAD 804cbf71b137895ade3bc9ffb0a41820bda677f7, E2E run 44657575196,
job 136027512542 SUCCESS, artefact public 14595075121 : 51/51 scénarios PASS,
5/5 receipts six vues froid/restart offline (960/1280/1300/1366/1600).
Chaque phase : 12 routes/variantes, 18 applications UI, 0 échec,
1 callback stale écarté ; 1 GET mock en phase froide, 0 nouveau GET au restart.
N3 GdkPixbuf : 1 création, 11 HIT froid ; 0 création, 12 HIT offline,
0 fallback. Décodage N4 p95 par résolution 1.39–3.59 ms froid et
1.31–1.49 ms offline ; maximum route→texture 200.82 ms froid et 161.15 ms
offline (horloge/polling inclus, pas un SLA). Mesures sur une seule Notice C,
pas un corpus GdkPixbuf 256 images.

PR #6960 (HEAD 6332906c4ff8dc01c5a89e0efac63c4058c885d4), PR CI
44657756748, AST 44657756719, E2E 44657756708 SUCCESS après relance de
l'installation APT ; merge dee4fb1c48b10483567098373774bdc157b3a2bb.
Ajoute mesures worker preview sans nouveau pool, avec exclusion queue,
admission overflow et annulation. Le raccordement des nouveaux percentiles
à la recette GTK et à l'export fermé est explicitement testé dans la suite.


### Réception GdkPixbuf six vues après #6964

HEAD 0a1e8bcfc223da9482176d12181fa282b4e9ff41, PR CI 44659139557 et E2E
44659139600 SUCCESS, merge ef590f5e42eec070f74acfe83fa00fc0e80e0109.
Artefact public 14595182382 lu : 51/51 PASS, cinq reçus (960 à 1600).
Worker création 3.75–3.92 ms (n=1 par résolution), HIT froid p95
0.79–1.50 ms (n=11), HIT offline p95 0.51–0.67 ms (n=12),
aucune création offline ni fallback ; N4 p95 froid 0.75–0.85 ms
et offline 0.74–0.80 ms. Ces valeurs ne sont pas un SLA.

### Budget et choix de non-optimisation

Inventaire de production : CoverLoader max_workers=6 par défaut et limites
HTTPX par client identiques, 2 threads preview, 2 EPUB physique ; preview
singleflight sur source/dimensions et maximum 64 jobs. Pas de nouveau pool.
HTTPX_TRANSPORT construit des clients à pools/cookies indépendants ;
aucun quota global transversal ou par hôte n'est garanti par ce seul port.
L'étude conclut à ne pas introduire de quota local présenté comme global.
La gouvernance transverse reste #2431 (ouverte), l'observabilité globale
#1741 ; contrats #6168 et preuves SHA physique #6936 séparés. Aucun
délai de queue confondu avec temps worker ni suppression du cache N2.

Le benchmark natif réutilise strictement le corpus 16/256 et les assertions
de #6962 dans le venv GTK existant, sans installer de dépendance ni admettre
de fallback Pillow. La mesure I/O reste celle du processus Linux, pas
celle d'un disque physique. La recette complète GTK demeure indépendante
du benchmark de conversion, sans modifier le paint ou le choix N1.
