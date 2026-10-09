# Simulateur éditeurs jeunesse — Usborne et l'école des loisirs

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
