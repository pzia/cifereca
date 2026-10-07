# Pseudo service Numilog

Bibliothèque Numilog fictive pour tests Cifereca.

Par défaut :

- 1 000 livres possédés ;
- 50 livres par page ;
- couvertures PNG générées/cache à la demande ;
- EPUB générés/cache à la demande via `simulation/common/fake_books.py` ;
- quelques liens expirés et parcours à redirections multiples pour tester les erreurs.

Lancement :

```bash
python3 simulation/numilog/pseudo_numilog_service.py
```

Configuration du plugin Numilog :

```
base_url = http://127.0.0.1:8094
email = numilog-test@example.test
password = test
```

Le plugin conserve `https://www.numilog.com/` comme valeur par défaut. HTTP
n'est accepté que sur loopback pour les tests locaux.
