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

## Prochaine tranche Plugins

1. Raccorder un contexte Chromium **possédé et isolé par profil**, SSO accompli
   par l'utilisateur. `ProfileScope.observed` doit provenir du propriétaire du
   navigateur, jamais d'une valeur lue dans le HTML. La projection ne vérifie pas
   elle-même l'identité authentifiée. Reconnexion = nouvelle époque et nouveau
   snapshot, aucune réutilisation aveugle des observations précédentes.
2. Extraire le DOM autorisé de façon bornée, constater l'URL finale, les erreurs
   HTTP/session et les sélecteurs réels ; introduire le manifeste read-only
   seulement avec un transport effectif, activation explicite et tests adaptés.
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
```

Le pont de collecte `tests/test_bnf_pret_web_dom.py` exerce les mêmes assertions
dans le gate Core existant malgré l'absence volontaire de manifeste. Il ne
modifie aucun runner. Tests synthétiques de contrat uniquement : aucune preuve
GTK, Chromium, fournisseur réel, emprunt ou fulfillment n'est revendiquée.
