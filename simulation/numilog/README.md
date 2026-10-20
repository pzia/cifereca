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

## Catalogue commercial public simulé (#771 / #1886)

Le serveur local expose aussi `GET /Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=...`, **sans connexion**, cookies ni droits d'acquisition. Il produit des notices et prix **entièrement fictifs** au format HTML minimal du parseur Numilog public (titre, auteur, éditeur, date, EPUB, prix 9,99 EUR), avec pagination `GRID_PAGE=0,1,...`. Les identifiants commerciaux `9000000000+` sont synthétiques : ne jamais consulter leurs liens sur Internet.

Seuls les tests peuvent ajouter `ux_fault=429|503|login_html|bad_grid` : 429 avec `Retry-After: 60`, 503, ou HTTP 200 non interprétable. Ces scénarios n'affectent pas `MesLivres`, `readBook` ou l'acquisition simulée.

```bash
python3 -m unittest discover -s simulation/tests -p 'test_numilog_public_simulation_contract.py'
```

Le test lance un serveur **127.0.0.1** éphémère, interroge ses pages publiques, puis passe le HTML au **vrai parseur** `cifereca_numilog_public.parse_public_search` avec une URL de provenance Numilog strictement utilisée comme donnée. Aucune requête n'est envoyée à Numilog réel ; aucun test GTK, navigateur public réel ou autorisation robots/CGU n'est revendiqué. Le fournisseur public reste désactivé par défaut en production en attente de qualification.

## Protocole privé : scénarios négatifs explicites (#771)

Les chemins fictifs `POST /Login.Aspx/AuthUser`,
`GET /Pages/Compte/MesLivres.aspx?page=N`,
`GET /Pages/Compte/readBook.ashx?lid=N`,
`GET /Services/Delivery.ashx?lid=N` et
`GET /downloads/N.epub|acsm` acceptent **uniquement en simulation**
le paramètre opt-in `ux_fault`. Sans ce paramètre, le parcours normal ne
change jamais. Les erreurs sont limitées à la route où elles sont demandées.

| Route existante | `ux_fault` | Résultat |
| --- | --- | --- |
| AuthUser | `auth_rejected`, `429`, `503` | Connexion refusée, limitation ou panne |
| MesLivres | `session_expired`, `429`, `503` | Session expirée, limitation ou panne |
| MesLivres | `bad_inventory`, `empty_inventory` | HTML inattendu ou zéro livre |
| MesLivres | `broken_next` | Page 1 normale, page 2 HTTP 503 |
| readBook / Delivery | `expired_link`, `429`, `503` | Lien expiré, limitation ou panne |
| Delivery | `missing_format` | HTML sans lien de téléchargement |
| downloads EPUB | `wrong_mime`, `invalid_epub` | MIME HTML ou faux EPUB corrompu |
| downloads ACSM | `wrong_mime`, `invalid_acsm` | MIME HTML ou XML tronqué |

Test contractuel HTTP (vrai serveur local, compte et livres fictifs) :

```sh
python3 -m unittest discover -s simulation/tests -p 'test_numilog_negative_protocol_p0.py' -v
```

Cette recette ne valide **ni** les vrais droits/acquisitions Numilog **ni**
une interface GTK. Aucune URL distante ni DRM n'est sollicité ; les anomalies
d'inventaire et de livraison ne sont jamais déclenchées par défaut.
