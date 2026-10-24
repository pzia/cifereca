# Baen — serveur public fictif de contrats

Le service `baen` sur **127.0.0.1:8100** sert des réponses synthétiques
déterministes, exclusivement pour les tests de l'adaptateur `provider.baen`.

```sh
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8100/health
curl 'http://127.0.0.1:8100/allbooks/category/index/id/2012?page=1&pageSize=2'
curl http://127.0.0.1:8100/allbooks/firefly-atlas
python3 simulation/datasetctl.py down ux-small
```

| Route | Contrat |
| --- | --- |
| `/health` | Seed, disponibilité et comptes synthétiques |
| `/allbooks/category/index/id/2012?page=N&pageSize=N&q=X` | Catalogue, recherche serveur, pagination, 0 résultat |
| `?q=auth-required`, `?q=access-denied` | HTTP 401 et 403, refus sans repli public périmé |
| `?q=rate-limit`, `?q=down` | HTTP 429, HTTP 503 |
| `?q=layout-change` | HTTP 200 non qualifié : résultat partiel, pas zéro attesté |
| Recherche `q=nothing` | Deux marqueurs exacts de zéro uniquement pour le corpus vide |
| `/allbooks/{slug}` | Détail WebScription : titre, auteur, date, ISBN, lien EPUB optionnel |
| `/downloads/{slug}.epub` | EPUB fictif valide généré localement, type MIME `application/epub+zip` |
| `/downloads/{slug}.epub?fault=invalid-epub` | Corps invalide pour contrôle du transport |
| Fiche inconnue ou sans téléchargement | 404 ou absence attestée de lien EPUB |

Aucun compte Baen, achat, livre privé ni accès au site réel n'est utilisé.

**Important :** le plugin Baen pointe volontairement vers son domaine HTTPS
officiel et ne possède **pas** de paramètre `base_url`. Pour ne jamais
rediriger la production, le catalogue de simulateurs déclare
`plugins=[]` et `fixture_packages=["baen"]`. Les tests injectent un
client `httpx.MockTransport` vers le serveur loopback. Les vrais résultats
ne sont pas insérés automatiquement dans le cache ou l'interface GTK.

Exécuter les tests de contrat :
```sh
python3 -m unittest discover -s simulation/tests -p 'test_baen_http_service.py' -v
```

Cet ensemble valide catalogue, fiches, délégation de téléchargement et
erreurs de contrat, **pas** un parcours applicatif GTK ni le vrai service
de transport HTTP. Le scénario `q=timeout` injecte un `httpx.ReadTimeout`
**dans le MockTransport du test**, sans sommeil, socket distante ni nouvelle
route serveur : il vérifie la classification Core, l'absence de retry et la
récupération après échec. Ce test ne mesure aucune latence réseau réelle.
