# OPDS — scénarios d'erreur explicites

Le serveur existant `simulation/opds/pseudo_opds_server.py` expose désormais
sur ses **deux véritables chemins de recherche** des scénarios déterministes
activés **uniquement** par le paramètre `ux_fault` :

| Valeur `ux_fault` | Réponse du faux serveur |
| --- | --- |
| `rate-limit` | HTTP 429, `Retry-After: 3` |
| `unavailable` | HTTP 503, diagnostic JSON fictif |
| `malformed` | HTTP 200, corps Atom XML tronqué |
| `wrong-root` | HTTP 200, XML bien formé mais non Atom |
| `empty` | HTTP 200, flux Atom vide (aucune notice) |
| `broken-next` | Première page avec lien `rel=next`, page suivante HTTP 503 |
| `redirect-loop` | HTTP 302 auto-référent |

Chemins : `/gallica/services/engine/search/opds` et
`/gutenberg/ebooks/search.opds/`. Les URLs sans `ux_fault` conservent
le catalogue normal.

Pour tester une acquisition, ajouter `?ux_fault=invalid-epub` ou
`?ux_fault=wrong-mime` sur un chemin fictif
`/{source}/epub/{id}.epub`. Ces réponses ont uniquement un but de recette :
le téléchargement réel et la publication dans la bibliothèque sont réalisés
par le plugin `transport.http`, pas par le simulateur.

Exemple :

```sh
python3 simulation/datasetctl.py up ux-errors
curl -i 'http://127.0.0.1:8080/gutenberg/ebooks/search.opds/?ux_fault=rate-limit'
curl -i 'http://127.0.0.1:8080/gallica/services/engine/search/opds?ux_fault=broken-next'
python3 simulation/datasetctl.py down ux-errors
```

Les tests de contrat `simulation/tests/test_opds_negative_contracts.py`
exécutent les **vrais** adaptateurs Gallica et Gutenberg sur la boucle locale.
Ils ne contactent jamais de domaine externe. Cela ne constitue **pas** un
test E2E de l'interface GTK, ni une vérification des services réels.
