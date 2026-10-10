# Service éditorial simulé (localhost seulement)

Ce service sert des **données inventées**, déterministes et réinitialisables.
Il est déclaré dans `simulation/services/catalog.toml`, et activé par les
profils `ux-small`, `ux-all` et `ux-errors` ; aucun plugin n'est forcé
du seul fait de cette activation.

```bash
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8097/health
curl http://127.0.0.1:8097/criticism/rss.xml
curl http://127.0.0.1:8097/criticism/jsonfeed.json
python3 simulation/datasetctl.py down ux-small
```

| Chemin | Usage et limites |
| --- | --- |
| `/health` | Seed, comptes synthétiques |
| `/criticism/index.html` | Découverte de RSS/JSON Feed via `<link rel="alternate">` |
| `/criticism/rss.xml` | Flux RSS 2.0 **compatible avec le vrai parseur Critique littéraire** |
| `/criticism/jsonfeed.json` | JSON Feed compatible avec le vrai parseur |
| `/criticism/empty.xml` | Source valide sans article |
| `/criticism/invalid.xml` | Flux XML tronqué pour gestion d'erreur |
| `/criticism/unavailable` | HTTP 503 déterministe |
| `/criticism/cases.json` | JDD de rattachement positif, ambigu, négatif |
| `/youth/records.json` | Fixture Jeunesse, **pas une API d'éditeur tiers** |
| `/constellations/records.json` | Fixture graphe, **pas une API Constellations** |

Les livres et critiques ne sont **pas réels**. Les URLs sources
`https://ux-fixtures.invalid` restent volontairement injoignables.
Le serveur accepte uniquement un bind loopback, n'autorise aucun chemin de
fichier arbitraire et ne modifie aucun cache applicatif.

Les profils déploient des fichiers éditoriaux dans leur runtime isolé
(`CIFERECA_UX_SPECIALIZED_DATA`) et le serveur reconstruit de façon
déterministe les mêmes JDD sous `service-cache/editorial/`. Ce sont deux
copies internes au même environnement isolé.

L'intégration GUI, l'abonnement explicite dans l'écran Critiques, les sources
OpenLibrary et la simulation Baen demeurent des travaux
distincts (#771, #772, #774).

### Workflows Jeunesse additionnels

Les JDD spécialisés v2 ajoutent deux routes JSON :
`/youth/discovery.json` (sources candidates, doublons, mauvaises URLs,
âges explicites et revue manuelle) et `/youth/enrichment.json`
(preuve ISBN, ambiguïté, conflit d'édition, cloisonnement des identités).
Ce sont **des fixtures de contrat**, pas des APIs du plugin. Aucune
découverte distante ni insertion de cache n'est réalisée par le serveur.
