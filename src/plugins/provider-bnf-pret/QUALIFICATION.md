# BnF — Prêt numérique : qualification préalable

**19 octobre 2026 — accès lecteur API non qualifié.** Rôle pilote : Plugins, #2054 ; intégration dépendante #2055.

## Décision de sécurité

La BnF permet l'accès au portail de prêt aux détenteurs d'un Pass lecture/culture ou recherche valide. La FAQ indique que les quotas sont affichés **après connexion**. Son API v1 exige séparément une authentification **HTTP Basic par nom d'utilisateur et clé API distincte du mot de passe**. Aucune documentation consultée ne démontre que cette clé est remise à un lecteur ordinaire, que le SSO Pass donne droit à l'API, ou que son identité correspond à un member_id de l'API.

**Décision : ne pas activer de client HTTP, d'authentification, de prêt, réservation, récupération ACSM ni restitution.** Ne jamais transformer un secret institutionnel en identifiant personnel, lancer un emprunt pendant une recherche/synchronisation, ni afficher un quota inventé. Fournir uniquement le lien officiel https://pret.bnf.fr/ tant que le parcours utilisateur autorisé n'est pas établi.

Le module Python livré ici est un **contrat offline non installé**, sans manifeste de plugin, sans réseau, sans droits déclarés (READER_CAPABILITIES vide) : il ne remplace pas le plugin BnF Catalogue général SRU et ne prétend pas fournir le service de prêt.

## Matrice endpoint × permission × données × preuves

**D** : documentaire officiel ; **T** : tests fixtures offline ; **L** : requête live explicitement autorisée et vérifiée. **Aucun L réalisé** et les permissions d'un lecteur particulier sont **inconnues**.

| Endpoint API v1 | Authentification / droits | Champs et états observables dans la documentation | Preuve / statut |
| --- | --- | --- | --- |
| GET /v1/resources.json | Basic username/api_key ; lecteur : ? | q, title, author, publisher, isbn des médias, start_at, page ; ressources/médias/licences, disponibles, total, réservations ; en-têtes X-Total-Items, X-Total-Pages, X-Per-Page, X-Current-Page ; 404 page hors limite | D+T, aucune requête L |
| GET /v1/resources/{id}.json | Basic ; lecteur : ? | détail, média, ISBN de l'édition numérique ; 404 resource_not_found | D+T de la forme, L absent |
| GET /v1/loans.json | Basic ; membre : ? | member_id (facultatif côté API), dates, page ; identifiant de prêt, compte, ressource, échéance ; le prototype impose un membre attendu et rejette toute divergence d'identité | D+T, L absent |
| POST /v1/media/{medium_id}/loans.json | Basic + member_id requis ; droit de créer : ? | **201** nouveau prêt, **200** existant et échéance inchangée ; 403 loans_limit_reached, no_loan_available, limites journalières/mensuelles ; 404 medium_not_found ; return_url facultatif lors de la création | D+T **interprétation pure** ; aucun POST |
| GET /v1/loans/{loan_id} | Basic + prêt déjà créé ; lecteur : ? | téléchargement ACSM ; 403 loan_expired/loan_hold_expired/limites ; 404 loan_not_found ; retélécharger n'équivaut pas à créer un prêt | D+T erreurs ; GET ACSM interdit |
| Réservation, annulation, restitution, liaison ADEPT | Aucun protocole API lecteur établi | workflow, callback, quotas, statuts à qualifier | Non implémenté |

**Invariants :** copie disponible ≠ quota lecteur ≠ autorisation Pass ; ressource catalogue ≠ prêt personnel, jamais owned ; lien de prêt/ACSM signé absent des notices, logs et sorties ; pas de « Renouveler » ; suppression de fichier local ≠ restitution. Un retour ADEPT accepté ne prouve ni retour BnF en ligne ni quota libéré. Le return_url décrit par la doc est un paramètre du POST de création, pas un endpoint appelable arbitrairement par le client.

## Prototype et tests

- Contrats sur pages 0/1/N, métadonnées auteur/éditeur/média/ISBN, disponibilité explicite ou inconnue, extrémités numériques et pagination incohérente ; pas de requête réseau.
- Comptes multiples : chaque prêt normalisé exige le même member_id attendu, pas de mélange intercompte ; aucun historique de téléchargement ni URL signée restitué.
- Erreurs métier explicites et erreurs 401, 403, 404, 429, 5xx avec messages sûrs ; codes inconnus jamais réémis.
- Simulation de résultats 200/201 (existence vs création, pas de renouvellement), validation d'origine du lien avant qu'il soit **jeté**, interdiction de toute capacité tant que les droits ne sont pas établis.
- Suite isolée : pytest -q src/plugins/provider-bnf-pret/tests ; 20 tests passés localement le 19/10/2026. Aucun test fournisseur réel ni recette CoreUI effectués.

## Dépendances restantes / sortie #2054

1. Obtenir de la BnF ou de l'opérateur Cantook les **droits d'un lecteur Pass**, la délivrance éventuelle d'une clé API, les CGU/scopes, l'identité member_id et le protocole SSO, par confirmation explicite. Ne pas tester un login personnel comme clé HTTP Basic.
2. Si API ou parcours utilisateur autorisé démontré, implémenter un transport HTTPS à origine contrôlée, sans redirects hors autorisation, GET retries bornés, TTL et isolation des comptes, diagnostics expurgés, puis capacités réelles et vérification sur compte consentant.
3. Confier au rôle Core les éventuels contrats identities/acquisition et au rôle UX la simulation fonctionnelle ; le rôle CI/CD aura la responsabilité du merge et des gates. **En l'absence de preuve, le plugin reste inactif et redirige vers le portail.**

## Sources datées (consultées le 19/10/2026)

- API générale : https://pret.bnf.fr/docs/api/general
- Ressources : https://pret.bnf.fr/docs/api/resources
- Prêts : https://pret.bnf.fr/docs/api/loans
- FAQ : https://www.bnf.fr/fr/centre-d-aide/bnf-pret-numerique
- Mode d'emploi : https://www.bnf.fr/fr/centre-d-aide/bnf-pret-numerique-mode-demploi
- Exploration GitHub : https://github.com/xlcnd/isbnlib-bnf (Catalogue général) et https://github.com/Kryzo/mcp-bibliotheque_nationale_de_France (Gallica). Aucun de ces clients ne démontre les permissions de prêt via API v1.
- Références projet : src/plugins/provider-bnf-catalogue (SRU public), src/plugins/provider-paris (PNB) ; #976, #982, #984, #1063. 
