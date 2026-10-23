# État actuel — décision produit du 23 octobre 2026

**Le parcours Web lecteur remplace la piste API/OPDS pour #2055.** Les conditions
d'habilitation API et la qualification ci-dessous sont historiques ; elles ne
bloquent plus le développement Web. Voir [WEB.md](WEB.md) pour la projection DOM
read-only, ses tests synthétiques et les limites restant à livrer. Les modules
API/OPDS historiques ne sont pas consommés par l'adaptateur Web.

---

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

## Découverte complémentaire — candidat OPDS 2.0 (19/10/2026)

URL signalée : **https://pret.bnf.fr/v1/home.opds2**. La convention d'extension suggère un flux OPDS 2 mais **aucune réponse HTTP en provenance de cet endpoint n'a été obtenue** dans l'environnement d'investigation (outil Web : accès désactivé à ce document ; test local : résolution DNS indisponible). **Ni HTTP 200, ni Content-Type, ni mode de connexion anonyme/SSO n'ont été confirmés.**

Cette piste est **distincte** de l'API REST v1 à clé HTTP Basic ci-dessus et de Gallica OPDS 1 / BnF Catalogue SRU. Si le serveur retourne un vrai flux OPDS 2, les collections navigation, groups, publications, les liens search templated, next et les couvertures constituent une voie de lecture du catalogue à tester **en GET non authentifié autorisé d'abord**, puis avec le mécanisme d'authentification documenté par le flux s'il existe. Une réponse HTML de login, un 401 ou un 403 n'est **pas** un catalogue public. Aucun lien borrow OPDS ne vaut habilitation au POST, acquisition ou ACSM.

Le module cifereca_bnf_pret_opds2.py fournit **uniquement le normaliseur hors réseau** : origines exactes de navigation https://pret.bnf.fr/v1/, paramètres de recherche connus, bornes de collections, groupes, pages, notices/titres/auteurs/couvertures, erreurs typées et signal d'authentification ; il exclut les URL de prêt, liens signés et toute conversion en droit personnel (owned=false, borrowable=false). Aucun manifeste n'active encore ce provider. Tests : pytest -q tests/test_bnf_pret_opds2_2054.py dans la CI existante.

**Qualification live encore nécessaire :** collecter pour l'URL fournie statut/Content-Type/Location, réponse JSON expurgée (seulement clés et liens sans tokens), différence sans session vs session lecteur consentie, document d'authentification OPDS éventuel, et réponse d'une recherche GET. Ne jamais collecter de cookie, nom de compte, token, lien ACSM signé. Le périmètre lecture seule ne doit être promu dans #2055 qu'après preuve du parcours et des droits.

Sources OPDS : https://specs.opds.io/opds-2.0 ; https://registry.opds.io/rel.html ; https://drafts.opds.io/best-practices/borrowing.html .

## Prototype et tests

- Contrats sur pages 0/1/N, métadonnées auteur/éditeur/média/ISBN, disponibilité explicite ou inconnue, extrémités numériques et pagination incohérente ; pas de requête réseau.
- Comptes multiples : chaque prêt normalisé exige le même member_id attendu, pas de mélange intercompte ; aucun historique de téléchargement ni URL signée restitué.
- Erreurs métier explicites et erreurs 401, 403, 404, 429, 5xx avec messages sûrs ; codes inconnus jamais réémis.
- Simulation de résultats 200/201 (existence vs création, pas de renouvellement), validation d'origine du lien avant qu'il soit **jeté**, interdiction de toute capacité tant que les droits ne sont pas établis.
- Suite isolée : pytest -q tests/test_bnf_pret_contract_2054.py ; 20 tests passés localement le 19/10/2026. La suite se trouve dans tests/ pour passer dans le gate Core de la PR CI, car le package d'étude sans manifeste n'est volontairement pas découvert par le lanceur plugins. Aucun test fournisseur réel ni recette CoreUI effectués.

## Décision de fin de qualification #2054 (19/10/2026)

**Décision : accès API v1 du lecteur ordinaire NON HABILITÉ / non démontré,
donc aucun connecteur BnF-Prêt activable.** Il ne s'agit **pas** d'une
preuve d'impossibilité technique définitive. Une habilitation institutionnelle
ou un parcours SSO documenté pourrait rouvrir la voie d'intégration **dans le
ticket existant #2055**. En attendant, garder le site officiel comme seul
parcours de prêt autorisé et la contribution sans manifeste ni commande HTTP.

Les sources officielles suivantes sont consultables en lecture documentaire
en date du 19/10/2026 :

- [Concepts généraux, authentification Basic séparée](https://pret.bnf.fr/docs/api/general)
  : `username/api_key` à chaque appel, clé d'API indépendante du mot de
  passe ; aucun mécanisme d'obtention de cette clé par un titulaire de Pass
  n'y est démontré.
- [Ressources v1](https://pret.bnf.fr/docs/api/resources) : recherche
  `q/title/author/publisher/isbn`, réponse `resources/media/licenses`,
  disponibilité des copies, pagination et erreur de ressource.
- [Prêts v1](https://pret.bnf.fr/docs/api/loans) : lecture
  `GET /v1/loans.json`, création **POST avec member_id**, `201` nouveau
  prêt / `200` prêt déjà existant, récupération ACSM *après* prêt ;
  `return_url` configure une notification POST au retour anticipé,
  et ne fournit **aucun** endpoint d'autorisation côté lecteur.
- [Mode d'emploi BnF](https://www.bnf.fr/fr/centre-d-aide/bnf-pret-numerique-mode-demploi)
  : le lecteur détient un Pass valide et se connecte à son **espace
  personnel** par mot de passe/lecteur de carte, pas par la clé API v1.

**Nature des preuves :** documentation officielle (**D**) pour ces
endpoints ; simulations locales sans réseau (**T**) pour les formes et
erreurs des contrats ; **aucun accès live à `/v1` ni compte Pass (**L=0**)**.
Les exemples JSON de la documentation sont des exemples, pas des réponses
obtenues à partir du compte de l'utilisateur. `/v1/home.opds2` reste une
piste, sans HTTP 200/type/permission attestés.

### Tableau d'écarts : les quatre autorités ne sont pas substituables

| Source / fournisseur existant | Identité et transport attestés | Catalogue et fiches | Prêts / droits | Écart à lever **dans #2055 seulement** |
| --- | --- | --- | --- | --- |
| `provider.bnf-catalogue` | SRU BnF Catalogue général public | Notices de catalogue papier/numérique | **Aucun compte**, ni ACSM, ni prêt numérique | Ne pas convertir un ISBN BnF Catalogue en droit BnF-Prêt ; recherche de disponibilité propre au compte |
| `provider.gallica` | OPDS/SRU Gallica (public, selon branche) | Documents numérisés/publics | **Aucun quota Pass BnF-Prêt** | Gallica et BnF-Prêt sont deux catalogues et licences distincts |
| `provider.paris` | Syracuse/PNB Paris, compte lecteur Paris distinct | Notices et disponibilité Paris | Prêts PNB sous identité Paris ; ADEPT si contractuellement offert | Ne réutiliser ni cookie Paris, ni `member_id`, ni autorisation du PNB Paris pour BnF |
| `runtime.kobo-adept` / retour ACSM | Contrat exécutable ADEPT, identité Adobe locale | **Pas** un catalogue de disponibilité/quotas | Retour de licence local/Adobe, **pas** preuve d'une restitution ou d'une libération de quota au portail BnF | Comparer séparément état ADEPT, prêt en ligne BnF et quota du lecteur, sans répétition automatique |
| **BnF-Prêt API v1** (contrat offline non activé) | Basic `username/api_key` **documenté pour l'API**, habilitation lecteur inconnue ; Pass/SSO séparé | Ressources, médias et licences en JSON, selon permission API | `member_id` requis pour création, aucun `owned` prouvé par ressource seule | Obtenir confirmation institutionnelle de l'accès, relation `member_id`/Pass et gestion des consentements |
| **BnF-Prêt OPDS 2** (contrat offline) | Endpoint candidat `/v1/home.opds2`, auth non attestée | Navigation/recherche possibles seulement si réponse conforme et autorisée | Aucun `borrowable` ni lien ACSM activable | Capturer réponse expurgée *autorisée* ou consigner 401/403, sans contourner l'identité |

### Protocole de recette consentie — **non exécuté**

1. **Autorisation écrite** BnF/opérateur Cantook : service API réservé à
   quel acteur (institution, intégrateur, lecteur) ? Méthodes autorisées,
   gestion d'une éventuelle clé, scopes et CGU ; refuser l'essai si l'acteur
   n'est pas habilité. À défaut, décision « accès impossible à ce stade »
   maintenue ; aucun secret d'institution sollicité auprès d'un lecteur.
2. **Lecture anonyme autorisée seulement** : vérifier par requête GET
   consentie `/v1/home.opds2` (statut, type, redirection, réponse
   expurgée) puis `resources.json` **uniquement si** permission
   documentée. Un login HTML ou 401/403 signifie absence de preuve de
   catalogue anonyme ; ne pas crawler/rejouer la session.
3. **Compte test volontaire et Pass en cours** si et seulement si le
   fournisseur documente un parcours d'app autorisé : associer identité
   SSO, `member_id` et permission effective ; vérifier 0/1/N livres
   sur page, pages suivantes, filtres ISBN/auteur, offre/quotas depuis
   les valeurs réellement renvoyées, 401/403/429/5xx et deux identités
   indépendantes. Ne jamais enregistrer cookies, secret API, URL de
   téléchargement ou valeur personnelle dans les fixtures et journaux.
4. **Mutation test autorisée distincte** après habilitation explicite :
   sur média de test et geste humain, distinguer un `POST` **201**
   d'un `POST` **200** sans prolonger le prêt ; ensuite seulement
   récupérer l'ACSM par URL autorisée. `return_url` doit être qualifié
   (émetteur, authentification/signature, retries, corps POST) avant
   toute mise en place d'un récepteur.
5. **Restitution vérifiée, jamais déduite** : vérifier trois états
   (succès ADEPT, prêt en ligne, quota Pass) séparément, sans relancer
   automatiquement un retour ni enregistrer les URL privées. Comparer
   des deux comptes, expiration et indisponibilité.
6. Après cette recette seulement, **#2055** pourra déclarer des capacités
   Core/Plugin, leur accès utilisateur et les actions explicites.
   Ici `READER_CAPABILITIES=frozenset()` et aucun manifeste installé.

### Traçabilité des critères du ticket d'investigation

- **Matrice endpoints/auth/scopes/champs/erreurs/preuve** : section
  « Matrice endpoint » ci-dessus ; ressources et prêts v1 documentés,
  OPDS2 candidate séparée. D/T/L explicités ; aucun HTTP utilisateur.
- **Décision sur secret institutionnel et accès** : Basic réservé aux
  détenteurs de la clé, lecteur non établi ; **aucune réutilisation de clé**.
- **Contrats et tests 0/1/N, erreurs, pagination, identités** : deux
  normaliseurs Python hors réseau et
  `tests/test_bnf_pret_contract_2054.py` /
  `tests/test_bnf_pret_opds2_2054.py`.
- **Comparaison vs BnF Catalogue, Paris, Gallica, ADEPT** : tableau ci-dessus.
- **Protocole utilisateur consenti et ticket d'intégration** : séquence
  non exécutée ci-dessus, intégration **#2055 déjà existante**.

**Le ticket d'investigation #2054 peut être clos sur la décision
« non habilité à ce stade », sans fermer #2055 ni revendiquer un accès
ou une acquisition numérique effectivement fonctionnels.**

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
