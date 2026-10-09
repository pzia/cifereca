# Simulateur éditeurs jeunesse — Usborne, école des loisirs et Milan

Serveur HTTP de données **entièrement fictives**, lié à `datasetctl` et
disponible uniquement sur l'interface loopback (port 8099 par défaut).

```bash
python3 simulation/datasetctl.py up ux-small
curl 'http://127.0.0.1:8099/health'
curl 'http://127.0.0.1:8099/publishers/usborne/us/books/browse-by-age/3-5-years'
curl 'http://127.0.0.1:8099/publishers/ecole/recherche-avancee?types=livre&page=1'
python3 simulation/datasetctl.py down ux-small
```

Contrats testés contre les **vrais adaptateurs**, par injection explicite
d'un `httpx.MockTransport` sans modification des URLs HTTPS et
des restrictions de domaines des plugins :

- **Usborne** : doublons liés à un badge, âge `3+`, `10+` et `18+ months`,
  ISBN valide, pagination explicite `?p=2`, page finale vide, 503,
  MIME inattendu et réponse trop volumineuse.
- **L'école des loisirs** : recherche `types=livre`, âge `3 à 6 ans`,
  `13 ans et +`, `indéterminé`, auteurs, ISBN, lien de pagination,
  résultat vide, 503, MIME inattendu et réponse trop volumineuse.

Les réponses ne sont **pas des pages réelles ni des données sources**.
Le simulateur n'introduit aucune nouvelle capacité applicative, ne modifie
aucun domaine de production, ne remplit pas directement les caches des
plugins et **ne prouve pas le rendu GTK E2E**. La suite est suivie dans #800
et #774.

## Milan (#813)

Le même serveur expose le catalogue `/publishers/milan/livres/`, sa page
`/publishers/milan/livres/page/2/` et les fiches `/publishers/milan/livres/1001-lune/`
à `1009-neuvieme`. La première page propose neuf fiches afin de mesurer
la borne de huit inspections du consommateur ; la seconde en propose une,
sans lien suivant. Les pages 3 à 100 sont vides.

Les fiches comprennent titre, auteurs, collection, thèmes, âges explicites
(1, 3, 8, 12 ans), âge absent malgré une collection `9-12 ans`, âge adulte,
ISBN absent et ISBN invalide. Un lien hors domaine et un doublon « Lire la
suite » sont également présents. Les noms et titres sont synthétiques.

```sh
curl 'http://127.0.0.1:8099/publishers/milan/livres/'
curl 'http://127.0.0.1:8099/publishers/milan/livres/1001-lune/'
curl 'http://127.0.0.1:8099/publishers/milan/livres/?fault=503'
python -m unittest discover -s simulation/tests -p test_milan_http_service.py
```

Chaque route accepte `fault=404`, `503`, `wrong-type`, `oversized` (>2 MB)
et `malformed`. Une fiche inconnue renvoie 404 ; une page numérique hors
1–100 renvoie 400. Les erreurs sont déterministes et sans état partagé
entre lecteurs.

Pour recetter le vrai `MilanYouthSource`, injecter `httpx.MockTransport` :
valider que l'URL demandée reste en HTTPS sur `www.editionsmilan.com`, puis
transférer uniquement son chemin vers `http://127.0.0.1:8099/publishers/milan`.
Le transport peut ajouter `?fault=...` à **la requête locale** pour les erreurs.
Ne pas remplacer `BASE`, `CATALOG` ou les allowlists de production.

La recette locale attend quatre notices sur la première page avec neuf
requêtes (catalogue + huit fiches), une notice en page 2, zéro en page 3 ;
`search("lune")` doit effectuer deux requêtes et `browse(age=3)` rendre deux
notices. Les paramètres de recherche restent locaux. Les tests du
consommateur restent la responsabilité du rôle Plugins dans son package ;
les self-tests ici vérifient uniquement le serveur de fixtures.

Les profils `ux-small`, `ux-all` et `ux-errors` activent déjà ce serveur.
Cela ne branche pas Milan automatiquement dans la GUI : **aucun parcours
GTK, cache ou changement d'identité n'est validé par ces fixtures HTTP**.
