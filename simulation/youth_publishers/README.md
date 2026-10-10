# Simulateur éditeurs jeunesse — l'école des loisirs

Serveur HTTP de données **entièrement fictives**, lié à `datasetctl` et
disponible uniquement sur l'interface loopback (port 8099 par défaut).

```bash
python3 simulation/datasetctl.py up ux-small
curl 'http://127.0.0.1:8099/health'
curl 'http://127.0.0.1:8099/publishers/ecole/recherche-avancee?types=livre&page=1'
python3 simulation/datasetctl.py down ux-small
```

Contrats testés contre les **vrais adaptateurs**, par injection explicite
d'un `httpx.MockTransport` sans modification des URLs HTTPS et
des restrictions de domaines des plugins :

- **L'école des loisirs** : recherche `types=livre`, âge `3 à 6 ans`,
  `13 ans et +`, `indéterminé`, auteurs, ISBN, lien de pagination,
  résultat vide, 503, MIME inattendu et réponse trop volumineuse.

Les réponses ne sont **pas des pages réelles ni des données sources**.
Le simulateur n'introduit aucune nouvelle capacité applicative, ne modifie
aucun domaine de production, ne remplit pas directement les caches des
plugins et **ne prouve pas le rendu GTK E2E**. La suite est suivie dans #800
et #774.

## Compléments nourrissons, recherche et erreurs (#800)

L'école des loisirs propose `Tout petit` avec l'âge explicite `0 à 3 ans`
en seconde page. Le filtre `search_api_views_fulltext` s'applique au titre,
sans tenir compte de la casse, **avant pagination** (deux résultats par
page). Ainsi `LOUP` retourne une fiche, `petit` deux, et toute recherche
sans correspondance retourne une liste vide sans lien suivant. Les filtres
sont indépendants et ne modifient pas le catalogue. Ce contrat synthétique
ne prétend pas reproduire le moteur de pertinence du site réel.

L'endpoint renvoie 400 pour une page non numérique ou hors
1–100, au lieu de convertir silencieusement les bornes. `fault=malformed`
renvoie un HTML tronqué déterministe, en complément des erreurs 503,
MIME et taille existantes. Aucun de ces tests n'atteste un parcours GTK.

```sh
python -m unittest discover -s simulation/tests -p 'test_publisher_http_edges.py'
```
