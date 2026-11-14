# P0 #5376 — témoins de mutation hors réseau

`scripts/ci_security_mutation_campaign.py` exécute une campagne volontairement
**bornée**, sans modification des sources de production : chaque mutant est
recompilé dans un processus pytest enfant à partir du HEAD testé. La baseline
des témoins doit être verte ; seuls les échecs d'assertion pytest (code 1,
résultat `FAILED`) prouvent que le mutant est tué. Un échec de collection,
d'import ou d'environnement n'est pas compté comme protection détectée.

| Site muté | Invariant observé | Témoins |
|---|---|---:|
| `public_httpx_transport._public_socket` — DNS privé accepté | Aucune socket privée après résolution | 1 |
| `public_httpx_transport._public_socket` — pair connecté non contrôlé | Rebinding DNS / pair réel refusé | 1 |
| `attested_https_delivery._admitted` — consentement ignoré | Refus avant second GET et avant publication | 2 |
| `attested_https_delivery.download_attested_https` — comparaison SHA finale neutralisée | Modification des octets en place, inode et taille inchangés, refusée | 1 |

**Quatre sites de mutation distincts, cinq témoins comportementaux.** Les
cinq sont des tests existants de la suite Core, avec DNS/socket
factices, HTTPX MockTransport et répertoires temporaires.

Commande dans un environnement de tests dédié :

```sh
python3 -m scripts.ci_security_mutation_campaign --json
```

Le verdict n'est pas une mesure globale de mutation ni une recette avec
fournisseur réel. L'identité de compte, le CAS métier et SQLite/recovery
nécessitent leurs propres témoins ; ce lot n'en revendique pas la couverture.
