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


## Extension N2 indépendantes / N3 mutualisé

Même commande, 16 et 256 DocumentRef distincts, huit notices par URL
(2 et 32 médias). Deux workers inchangés. Le MockTransport attend une
barrière explicite avant de retourner les octets : les huit demandes
concourantes ne peuvent pas masquer un double GET derrière un HIT déjà chaud.
Trois phases : N2/N3 froids, chauds, puis nouvelle révision sur une seule
notice par groupe et média inchangé. Assertions : 16/256 décisions, 2/32 GET
et 14/224 coalescences à froid ; aucun GET supplémentaire ; 2/32 MISS N2
lors de l'invalidation ciblée et tous les autres HIT. La sortie
COVER_SHARED_N3_BASELINE documente p50/p95/max callback, CPU, RSS cumulatif,
octets logiques publiés et compteurs /proc/self/io si disponibles.
Les délais incluent la barrière et la queue : aucune comparaison de SLA avec
la série séquentielle. Corpus synthétique, pas de GTK ni d'I/O physique
mesurés. Ne pas déduire une identité documentaire du partage N3.


## Mesure complémentaire du curseur SQLite C (#1023)

`pytest -s tests/test_core_p0_2435_public_c_cursor_baseline.py`
mesure 16/256 notices canoniques exactes, moitié correspondantes, huit URL
partagées, pagination complète des seuls résultats connus (50 par page).
Trois états : observations live, moitié évincées par CAS, puis toutes
évincées et nouveau Storage. Neuf parcours par état ; p50/p95/max, CPU
cumulatif, RSS high-water et I/O du processus. Un passage séparé avec trace
SQL compte les statements et lectures de fence, sans tracer les valeurs.
La trace n'est pas active pendant les mesures de latence. Assertions :
identités C et ordre stables, aucun droit, aucune mutation du curseur.
Le nombre de lectures fence est une mesure (pas une assertion de
performance ni une réception de #7123). Comparer avec le même scénario
après intégration de #7123 ; ne pas attribuer un écart au seul contrôle
sans exécution comparable. Aucune revendication de bibliothèque réelle,
de disque froid, de rendu GTK ni de SLA.


## Reçu terrain N3 EPUB (opt-in, Linux)

Sur deux échantillons locaux sélectionnés par le propriétaire, 16 et 256
fichiers distincts, préparer deux manifestes JSON (tableaux de chemins absolus
EPUB/KEPUB explicitement autorisés). **Ne pas joindre les manifestes au ticket.**
Dans un processus séparé avec les dépendances Cifereca :

```sh
PYTHONPATH=src python3 scripts/benchmark-notice-cover-epub.py --size 16 --manifest /tmp/epub16.json
PYTHONPATH=src python3 scripts/benchmark-notice-cover-epub.py --size 256 --manifest /tmp/epub256.json
```

Partager seulement les deux objets JSON agrégés et le commit exécuté, avec le
type de disque et le contexte de charge si connus. Aucune ouverture de
bibliothèque SQLite ou session réseau. Lecture seule des archives nommées,
no-follow final et taille bornée (2 Gio par archive, 32 Gio cumulés) ;
cache N3 temporaire supprimé en fin de processus ; pas de nettoyage du cache
système ni de mutation des originaux (les lectures peuvent modifier atime).
Les références sont des scopes d'expérience, non des preuves de Placement.
Les refus/échecs sont comptés, jamais omis du dénominateur sélectionné ;
un corpus comportant peu de couvertures qualifiées n'est pas représentatif.
La préparation du reçu lit et hache l'archive : cache OS potentiellement chaud.
Les deltas Linux read_bytes/write_bytes sont des compteurs du processus,
pas une mesure directe des IOPS du matériel ; zéro read_bytes ne signifie
pas zéro lecture logique. Latences froid/chaud N3, CPU/RSS et cache physique
uniquement, pas GTK, ni comparaison de versions, ni SLA.
Exécuter exactement les mêmes manifestes avant/après une optimisation
sur un environnement comparable. Ce script ne suffit pas à recevoir
le ticket sans mesures terrain effectives.
