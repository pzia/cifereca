# Éditions Milan — simulation de contrat HTTP

Le faux service `milan` (port **8101**, écoute strictement sur `127.0.0.1`)
sert des contenus **synthétiques**, indépendants d'une connexion Internet.

```sh
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8101/health
curl http://127.0.0.1:8101/milan/livres/
curl http://127.0.0.1:8101/milan/livres/63047-moqueuse/
python3 simulation/datasetctl.py down ux-small
```

- `/milan/livres/` : titres + lien explicite vers page 2 ;
- `/milan/livres/page/2/` : livre sans âge explicitement attesté (rejet attendu) ;
- `/milan/livres/{id}-{slug}/` : âge éditeur, auteur, collection, thèmes et ISBN ;
- âge 1, 8, 18 ans ; âge absent malgré un titre de collection évoquant un âge ;
- ISBN valide/invalide, résultat vide, fiche inexistante (404) ;
- `?fault=unavailable` (503), `?fault=wrong-type`, `?fault=oversized`.

Le véritable `MilanYouthSource` impose `https://www.editionsmilan.com`
et ses chemins publics. Pour les tests, un `httpx.MockTransport`
redirige **uniquement** les requêtes autorisées vers loopback : aucun
DNS système, proxy ni paramètre de production n'est modifié.

```sh
python3 -m unittest discover -s simulation/tests -p 'test_milan_http_service.py' -v
```

Les profils `ux-small`, `ux-all` et `ux-errors` démarrent ce service
sans activer automatiquement le plugin ; ses données ne sont **pas injectées**
dans les pages GTK ou les caches métier. Il s'agit d'une couverture de
**contrats HTTP**, pas d'une recette GUI E2E (#816).
