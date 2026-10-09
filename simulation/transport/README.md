# Laboratoire HTTP — transport transversal

Serveur strictement **local** sur `127.0.0.1:8102` et sans requête externe,
destiné à la recette des erreurs du **vrai** `HttpTransport`.

```sh
python3 simulation/datasetctl.py up ux-small
curl http://127.0.0.1:8102/health
curl -I http://127.0.0.1:8102/status/429
python3 simulation/datasetctl.py down ux-small

python3 -m unittest discover -s simulation/tests -p 'test_http_negative_contracts.py' -v
```

Les scénarios sont déterministes, utilisent un EPUB fictif et ne comportent
aucun secret utilisateur. Dans la matrice de couverture, le service est une
**fixture HTTP** (`fixture_packages`), jamais un plugin activé automatiquement.

| Routes | Comportement attendu du plugin |
| --- | --- |
| `/file.epub`, `/redirect/relative` | Téléchargement et validation d'un EPUB fictif |
| `/status/429`, `/status/503` | Échec temporaire, pas de fichier publié |
| `/redirect/missing`, `/redirect/loop-a` | Pas de cible et limite de redirections |
| `/html`, `/wrong-mime`, `/empty` | Refus du contenu HTML, MIME interdit ou vide |
| `/invalid-epub` | Format EPUB non valide, publication interdite |
| `/declared-large`, `/stream-large` | Taille déclarée ou réellement reçue excessive |
| `/slow` | Timeout contrôlé et nettoyage du téléchargement |
| `/redirect/loopback-other?port=…`, `/inspect-headers` | Changement d'origine : suppression des en-têtes sensibles (le serveur ne renvoie que des booléens) |

Le refus des redirections **HTTPS vers HTTP** est testé séparément avec
`httpx.MockTransport` : le serveur loopback ne crée pas de certificats HTTPS.

Les tests vérifient le nettoyage des fichiers `.part` et la non-publication
après échec. Ils n'évaluent pas les comptes utilisateurs, la disponibilité
d'Internet, une UI GTK réelle ni le téléchargement DRM.
