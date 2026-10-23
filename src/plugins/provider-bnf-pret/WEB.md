# BnF Prêt — adaptateur DOM lecteur (#2055 P0)

Décision utilisateur du 23 octobre 2026 : le produit utilise le **parcours Web
lecteur**, avec SSO interactif puis consultation des pages du portail. Les études
API/OPDS de `QUALIFICATION.md` et des deux anciens modules sont historiques ;
elles ne constituent plus une dépendance de cette intégration.

## Tranche livrée

`cifereca_bnf_pret_web.py` est une projection **offline**, sans dépendance externe,
sans import Core, réseau, navigateur, stockage, acquisition ou formulaire soumis.
Elle n'est pas encore un provider installable. Aucun manifeste ne déclare des
capacités fonctionnelles fictives. Les anciennes études ne sont pas appelées.

| Projection | Structure documentée dans #2055 | Garantie / limite |
| --- | --- | --- |
| Suggestions | `.search-results__list .search-resource-result`, liens `/resources/{id}` | 0/1/N, déduplication par ressource ; homonymes distincts ; titre microdata ou texte de lien unique ; jamais une recherche exhaustive |
| Fiche | microdata `name`, `author`, `publisher`, `isbn`, `inLanguage`, `description` | Le garde `itemscope` / `schema.org/Book` est **synthétique, à qualifier** sur DOM réel ; absence/ambiguïté refusée ; noms imbriqués Person séparés du titre |
| Disponibilité | `.lb_unavailable`, `.button-borrow`, `.button-booking` | Marqueur d'indisponibilité textuel dans la fiche uniquement ; bouton seul = `unknown`, aucune capacité d'emprunt |
| Prêts | `.active-loans .js-loan`, liens `/loans/{id}` et `/resources/{id}` | Paire unique par bloc ; contexte explicite identité/compte/époque de session ; aucune URL signée conservée |

Les classes et noms de propriétés proviennent des **faits structurels expurgés**
du ticket ; tous les contenus de tests sont fabriqués. La structure exacte des
dates, des états serveur et de la pagination n'est pas qualifiée : **aucune date,
quota ou exhaustivité n'est inventé**. Une section vide reste `partial` ; une
page inconnue ou tronquée est refusée, jamais assimilée à une suppression.

`merge_observed_loans` accumule sans destruction les observations de la même
époque de session ; il refuse les contradictions, changements de profil/compte et
excès de volume. Ce résultat n'est **ni un ledger de droits ni une synchronisation
Core complète**. Une ancienne observation n'est pas un droit courant.

Bornes : 512 Kio UTF-8 par document (contrôle avant parsing), 12 000 nœuds,
64 niveaux, 100 notices/prêts et 4 096 caractères par valeur. Les éléments masqués
et scripts sont écartés ; aucun script n'est exécuté. Aucun lien n'est suivi.
Origine exacte HTTPS, chemins de ressource/prêt contrôlés, queries et fragments
jetés. Les données métier sont exclues des `repr` et des messages d'erreur.
Ces objets restent des données privées de l'application, **pas des payloads de
diagnostic** : ne pas sérialiser leurs champs vers les logs ou un LLM.

## Session navigateur lecteur

`cifereca_bnf_pret_browser.py` raccorde maintenant la projection à une session
Playwright/Chromium visible, créée sur action explicite. Elle possède un contexte
neuf et une époque aléatoire pour l'identité/compte locaux sélectionnés, sans
profil persistant, HAR, état exporté ni dépendance au Browser Recorder.

Le SSO est **manuel**. L'adaptateur ne lit ni formulaire, ni cookie, ni assertion.
Après confirmation humaine du compte utilisé, `finish_login` installe les règles
de lecture seule sur tout le contexte, puis charge l'activité. Cette confirmation
est une déclaration humaine, **pas une vérification automatique de l'identité BnF**.
Une page de prêts reconnue ne suffit pas à publier des droits dans le Core.

En lecture seule : POST/PUT/DELETE, WebSockets, navigation hors portail ou vers
SSO/déconnexion, routes de prêt/téléchargement et API historique sont bloqués.
Les sous-requêtes GET ne sont permises que pour les pages de lecture autorisées
et les types/extensions statiques connus. Une nouvelle dépendance du site peut
donc être refusée ; sa qualification doit précéder un élargissement. Aucun clic
automatique, retry, téléchargement ou retour n'est implémenté.

L'extraction JavaScript sélectionne une seule section utile et contrôle son
volume **dans le navigateur avant transfert vers Python**. Elle reconstruit un
HTML minimal : classes/propriétés permises, liens canoniques sans queries,
aucun champ de formulaire ni attribut actif. Pour les prêts, elle écarte tous les
textes et ne conserve que les liens structurants. Les gardes Python s'appliquent
à nouveau. Une navigation pendant l'extraction invalide le résultat.

### Essai local volontaire

Dans un environnement disposant déjà de **Playwright Python >=1.50,<2 et de son
Chromium**, lancer :

```bash
python src/plugins/provider-bnf-pret/cifereca_bnf_pret_browser.py
```

Le programme demande l'accord avant ouverture, laisse l'utilisateur se connecter,
puis demande confirmation du compte. Il affiche uniquement des compteurs et
états fermés ; aucune donnée privée ni snapshot n'est enregistré. Les commandes
`s` et `l` lisent les suggestions visibles ou rechargent l'activité ; `q` ferme
et détruit la session. La boucle traite les événements Playwright pendant la
saisie clavier, afin de laisser la navigation et ses gardes fonctionner.

API Python : `open_login(consent=True)` → SSO manuel →
`finish_login(account_confirmed=True)` → `suggestions`, `detail`, `loans`
avec `expected=session.scope` → `close()` dans un `finally`. Tous les appels,
y compris `pump()` depuis une future boucle UI/worker, restent sur le **même
thread propriétaire**. Une nouvelle connexion exige une nouvelle instance.

**Preuves actuelles :** tests de cycle de vie et extraction JS sous Node, ainsi que
2 tests de navigation Chromium réelle en CI sur pages totalement synthétiques.
**Aucune connexion SSO BnF réelle ni identité amont n'est validée.** Aucun manifeste
n'active encore un provider. Le raccordement Recherche/Emprunts/Sync reste à faire.

Deux tests d'acceptation **Chromium réel sur pages synthétiques** sont fournis,
exécutés par le gate PR CI isolé sur pages synthétiques :

```bash
CIFERECA_BNF_CHROMIUM_TEST=1 python -m unittest discover \
  -s src/plugins/provider-bnf-pret/tests -p test_browser_chromium.py -v
```

Ils interceptent toutes les requêtes et fournissent uniquement du HTML synthétique
(aucun accès BnF/IdP réel). Le navigateur headless de test vérifie l'extraction
effective et les refus réseau POST/prêt/API. Sans opt-in, ces tests sont **ignorés,
pas réussis** ; avec opt-in, une dépendance ou un binaire manquant fait échouer
le gate. Cette preuve ne remplacera pas la qualification fournisseur du SSO.

## Worker asynchrone lecteur

### Worker pour le broker public tasks

`cifereca_bnf_pret_worker.py` fournit une boucle de commandes pour le futur
raccordement UI. La soumettre une seule fois via
`tasks.submit(lambda control: worker.run(control, consent=True))`, uniquement
après action et consentement utilisateur. Le navigateur est créé, utilisé et
fermé sur ce même thread ; aucun appel Playwright ne part du thread GTK.

Attendre `worker.state == "login"`, puis conserver `worker.scope`. Les commandes
`confirm_account` (confirmation humaine explicite), `suggestions`, `detail` et
`loans` exigent toutes `expected=scope` et renvoient un Future privé. Elles sont
limitées à huit en attente ; aucun retry ni commande de mutation n'existe.
Les callbacks UI doivent être rapatriés sur GTK et **revérifier le scope actuel**
avant affichage. Le résultat de la tâche longue ne contient qu'un code fermé,
jamais un snapshot privé.

Appeler `worker.stop()` sur fermeture UI, changement d'identité ou déconnexion.
Cela invalide immédiatement le scope ; le thread propriétaire ferme ensuite le
navigateur. L'annulation TaskControl est également prise en compte. Un résultat
arrivant après annulation est refusé, les commandes en attente sont terminées
avec erreur fermée et une session fermée n'est jamais réutilisée. Cette boucle
ne déclare toujours aucun compte/droit Core et ne crée aucun widget GTK.

## Prochaine tranche Plugins

1. Qualifier dans Chromium réel le contexte éphémère et les sélecteurs, notamment
   le garde Book exact, les états vides et la recherche du portail. L'identité
   amont reste à vérifier au bon niveau ; ne pas transformer le scope local ou
   une déclaration humaine en preuve automatique d'un droit BnF.
2. Raccorder le cycle de vie à un worker/UI et à l'identité Core via l'API publique,
   avec annulation et changement de profil. Introduire ensuite le manifeste
   read-only avec activation explicite et tests d'installation adaptés.
3. Projeter recherche/prêts via `cifereca.plugin_api` ; qualifier les échéances et
   snapshots avant toute évolution du ledger. Ne pas importer de module Core
   privé ; une primitive publique manquante relève du propriétaire Core.
4. Ajouter ensuite confirmation d'emprunt, POST navigateur sans retry et
   vérification dans l'activité. Réservation/restitution restent hors MVP.
5. Observer le **vrai téléchargement** (nom, MIME, octets) avant remise ACSM à la
   chaîne ADEPT existante. `.bin` est un artefact du Recorder, **aucun import ou
   renommage spécial** n'est nécessaire ici. Aucune licence dans trace/ZIP/CI.

## Vérification reproductible

```bash
python -m unittest discover -s src/plugins/provider-bnf-pret/tests -v
python -m unittest discover -s tests -p test_bnf_pret_web_dom.py -v
python -m unittest discover -s tests -p test_bnf_pret_browser_session.py -v
python -m unittest discover -s tests -p test_bnf_pret_reader_worker.py -v
```

Les ponts de collecte `tests/test_bnf_pret_web_dom.py` et
`tests/test_bnf_pret_browser_session.py` et
`tests/test_bnf_pret_reader_worker.py` exercent les mêmes assertions
dans le gate Core existant malgré l'absence volontaire de manifeste. Il ne
modifie aucun runner. Les tests du worker sont synthétiques : aucune preuve GTK, SSO réel,
emprunt ou fulfillment n'est revendiquée.


## Page CoreUI locale expérimentale — opt-in

Le manifeste \`org.cifereca.provider.bnf-pret\` expose **uniquement**
\`ui.page.bnf-pret\`, désactivé par défaut. Aucune contribution
\`provider.library\` ni \`catalog.search\`/\`sync.loans\` n'est annoncée.
Après activation explicite et installation de Playwright/Chromium, la page
« BnF Prêt (lecture expérimentale) » associe sa session à **l'unique identité
Core active attestée**. En l'absence de profil unique, aucun démarrage n'est
possible. Changement de profil ou fermeture de page invalident immédiatement
les futures en vol, révoquent la session et ferment Chromium sur son thread.

Le consentement est recueilli avant l'ouverture. L'utilisateur effectue
son SSO lui-même dans le navigateur. Cliquer « J'ai terminé la connexion »
est une confirmation humaine, **pas** une validation automatique de compte
ou de droit BnF. Les commandes lisent les prêts et suggestions visibles,
avec seulement des compteurs/statuts fermés dans cette première interface.
Ces comptes sont toujours non exhaustifs et ne sont **jamais publiés**
dans Core ou Diagnostics. Aucune mutation, téléchargement, réservation ou
synchronisation silencieuse. Identifiant de compte du worker : simple
marqueur local, non assimilé à un identifiant BnF authentifié.

Tests de contrôle sans GTK/Playwright :
\`python -m unittest discover -s src/plugins/provider-bnf-pret/tests -p test_reader_page.py -v\`.
La CI Core collecte également \`tests/test_bnf_pret_reader_page.py\`.
La recette GTK/SSO réelle et la qualification des sélecteurs restent ouvertes.

## Observations de prêts en mémoire — session unique

`cifereca_bnf_pret_observations.py` accumule seulement les couples structurés
`loan_id/resource_id` d'une **même** identité/compte/époque de session.
La consultation retourne des **compteurs** : prêts présents sur la dernière page,
nouvelles observations et références vues depuis l'ouverture de la session.
Le cumul est une information historique de parcours, **jamais le total des droits
actifs**. Une page vide/partielle, une erreur HTTP, une déconnexion ou une
sélection différente ne supprime ni n'expire automatiquement un prêt.

Le collecteur refuse les contradictions, pages prétendument exhaustives,
identifiants invalides et plus de 100 observations. `close()` révoque le
contenu en mémoire : nouvelle session = nouvel objet, aucune persistence ni
écriture Core. La page BnF-Prêt consomme maintenant ces comptes après chaque lecture des prêts
validée pour la même session ; elle affiche distinctement les prêts de la dernière
page, les références nouvelles et toutes celles déjà vues dans la session.
Après une erreur, les observations antérieures restent visibles comme historiques,
non comme droits actuels. La fermeture ou le changement de profil efface le cumul.
**Aucune capacité `sync.loans` n'est encore déclarée**.

Tests : `python -m unittest discover -s src/plugins/provider-bnf-pret/tests -p test_loan_observations.py -v`.


## Diagnostic privé de structure DOM — qualification P0 #2055

Après ouverture volontaire de Chromium et confirmation manuelle du compte,
le lecteur expose « Diagnostiquer les sélecteurs visibles ». Cette action
n'effectue **aucune requête réseau** et n'inspecte ni valeurs de champs, ni
texte, HTML, URL, cookie, formulaire ou stockage. Une évaluation dans la page
ne renvoie que sept **compteurs de correspondances** de sélecteurs
actuellement employés (prêts, recherche, microdata Book, liens de ressources
et de prêts), plafonnés à 101 ; le résultat apparaît **uniquement dans l'UI**
de la session courante, jamais dans TaskBroker/Diagnostics/LLM.

L'extraction exige la session `readonly`, l'identité/compte/époque exacts,
une URL du portail autorisée et une page stable. Un résultat inattendu est
refusé plutôt que transmis. Les tests Chromium n'utilisent que des pages
HTML synthétiques interceptées. Ces comptes ne constituent **aucune preuve
d'identité distante, de droit actif, de complétude ou de synchronisation**.
En particulier « 0 blocs de prêts » signifie seulement que le sélecteur
actuel ne correspond à aucun nœud de la page lue : ne jamais en déduire
une restitution ou une absence de prêt. Pour la qualification sur parcours
réel, on peut noter les seuls compteurs locaux, sans joindre capture,
identifiants personnels, HTML, SAML, URL signée ou licence.
