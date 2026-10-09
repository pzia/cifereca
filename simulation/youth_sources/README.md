# Simulateur BookTrust + OpenLibrary (contrats de jeunesse)

Serveur **local** (127.0.0.1:8098 par défaut) de réponses **entièrement
synthétiques**, sans comptes ni requête vers les vrais fournisseurs.

```bash
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8098/health
curl 'http://127.0.0.1:8098/booktrust/book-recommendations/bookfinder/bookfinder-recommendations/?page=1'
curl 'http://127.0.0.1:8098/openlibrary/search.json?q=atlas&limit=10&page=1'
python3 simulation/datasetctl.py down ux-small
```

## Contrats servis

| Source | Routes de simulation | Cas |
| --- | --- | --- |
| BookTrust | `/booktrust/book-recommendations/bookfinder/bookfinder-recommendations/?ageGroups=...&bookfinderCategories[]=...&page=1` | HTML, filtres âge et thème, pages 1/2/3, 0 résultat |
| BookTrust | `/booktrust/book-recommendations/bookfinder/{slug}/` | Fiche HTML attestant séparément âge d'intérêt / âge de lecture |
| BookTrust | `?fault=unavailable`, `?fault=non-html`, fiche absente | HTTP 503, mauvais Content-Type, HTTP 404 |
| OpenLibrary | `/openlibrary/search.json?q=...&fields=...&limit=...&page=...` | Documents ISBN concordants, différents, sans ISBN ; page suivante vide |
| OpenLibrary | `/openlibrary/works/OL45804W.json` et `/openlibrary/books/OL7353617M.json` | Notices et éditions, **aucun âge inféré** |
| OpenLibrary | `q=rate-limit`, `q=broken-json`, OLID inconnu | HTTP 429, JSON invalide, HTTP 404 |

**Points de sûreté :** les plugins utilisent volontairement des URLs HTTPS
réelles avec listes de domaines autorisés. Le faux serveur **ne remplace
jamais** ces URLs en production. Les tests injectent un `httpx.MockTransport`
qui reçoit les requêtes du vrai plugin puis les redirige exclusivement sur le
serveur de test local et sans changement des règles de sécurité.

Les pages GUI Jeunesse ne sont pas alimentées automatiquement par ce
service : il couvre les contrats des adaptateurs, **pas** l'E2E GTK.
Les sources fictives ne sont pas des reproductions d'un contenu réel.
