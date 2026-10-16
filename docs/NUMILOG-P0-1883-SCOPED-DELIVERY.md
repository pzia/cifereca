# Numilog P0 #1883 — livraison ACSM depuis une fiche possédée

## Qualification de la page fournie (16/10/2026)

La page privée fournie a été analysée **hors réseau et en mémoire**. Elle
n'est ni une page `MesLivres` ni un script attaché à une fiche de cet inventaire :
son formulaire cible `/Pages/Commande/ConfirmationPaiement.aspx`, sans
`myTable` ni `LnkDownloadBook`. Un tableau JavaScript global `Links` contient
**deux objets pour un même couple produit/exemplaire**, variantes **LCP et
ADOBE**. Le bouton `ModalDownload` référence exactement ce couple. Aucune
équivalence `ProductId == lid` n'est supposée.

La comparaison a porté sur **le même HTML privé**, sans exécuter ses scripts
ni interroger un lien : `delivery_url` avant #1890 (main `4586b642`) le rejette
avec `ParseError`, car **deux URL d'origines autorisées** sont candidates.
La version livrée dans #1890 (`b2ede93b`) sélectionne **l'unique ACSM ADOBE**,
égal au candidat du couple du bouton ; l'autre URL autorisée est ordinaire.
Les seuls compteurs conservés sont `js_delivery_shapes=2`,
`allowed_candidates=2`, `attested_acsm_candidates=1`.

La différence HTML/parseur du critère de sortie #1883 est ainsi reproduite
sur la page fournie. **Aucun téléchargement réel ni fulfillment ADEPT n'a
été effectué.** L'analyse ne prouve pas la succession HTTP réellement
renvoyée à l'installation de l'utilisateur ni le succès de sa session.

Le complément 0.1.18 lie ce tableau global à **une seule carte** portant
le `readBook` exact (lid, variante et autres paramètres de query) choisi dans l'inventaire frais. Le
couple du bouton `ModalDownload` doit être celui de l'unique ACSM ADOBE.
Un autre produit/exemplaire, une variante différente, une carte dupliquée,
un tableau dynamique ou plusieurs déclarations sont refusés avant GET signé.
Cette vérification ne généralise pas le rattachement des anciens HTML sans
ce tableau : leur contrat historique de destination unique reste inchangé.

`tests/fixtures/confirmation_delivery_structure.html` conserve seulement
ces relations DOM/JS, avec contenu, identifiants, commandes, paramètres et
liens **entièrement inventés**. Ce fichier n'est pas une copie nettoyée du
HTML privé. Le test de transport utilise un HTTP 302 fictif depuis un
`readBook` sélectionné dans un inventaire frais, cette page intermédiaire,
un ACSM fictif et un seul import pour le propriétaire. Il vérifie aussi
zéro acquisition à l'inventaire, aucun GET LCP/navigation/autre livre,
l'absence de secrets dans les diagnostics, le refus de deux ACSM et dix
cas de rattachement incorrect (produit, exemplaire, readBook, variante, query, contrôle HTML encodé,
carte absente/dupliquée, tableau dynamique/dupliqué).

## Nouveau parcours sûr

1. Recherche, connexion et inventaire : `parse_library_page` ignore par défaut
   les objets JavaScript contenant les URL signées. `SearchResult` ne contient
   **aucune** URL ACSM ; aucun GET de livraison, ACSM ou téléchargement.
2. Sur **Télécharger** explicitement : `NumilogSession.download_direct`
   retrouve le `record_id` scoped du compte via une nouvelle lecture des
   pages privées ; elle active alors `delivery_evidence=True`.
3. Une URL de livraison n'est utilisable que lorsque l'objet JavaScript est
   un descendant structurel de **la même fiche DOM**, avec **un seul titre
   identifié** et une seule action `readBook.ashx`, et qu'un **unique** objet
   `ProductId / IndexExemplaire / DrmTechnlology: ADOBE / link` existe, et
   que ses identifiants concordent avec `data-product-id` /
   `data-index-exemplaire` lorsque ces attributs sont attestés. Le contrat
   **n'assimile jamais** `ProductId` ou `IndexExemplaire` au `lid` HTTP.
   Un script hors fiche, une association contradictoire ou ambiguë n'autorise
   aucune livraison directe.
   Pendant une acquisition, seuls les champs de livraison correspondant au
   **record_id exact sélectionné** sont décodés ; les autres livres de la même
   page ou des pages suivantes restent un inventaire **sans liens signés
   en mémoire**. Une fiche avec plusieurs titres ou une répétition du même
   `lid`/format **pour le livre ciblé**, dans une page ou entre pages, est
   refusée avant tout GET signé. Les répétitions d'autres livres ne bloquent
   pas l'acquisition de la fiche choisie ; le mode inventaire conserve sa
   déduplication historique.
4. En présence d'une unique preuve scoped à fraîcheur de l'action : seul un
   lien `HTTPS entrepot2r.numilog.com:443` de chemin exact
   `/fulfillment/URLLink.acsm` est accepté. Aucun paramètre de signature
   n'est conservé en catalogue. Chaque saut réseau reste soumis à
   `checked_url`, à l'allowlist stricte et aux limites MIME/taille/redirect.
5. Sans preuve de fiche, le parcours historique `readBook.ashx` s'applique.
   Les HTML intermédiaires, y compris une confirmation avec tableau global
   LCP/ADOBE, sont lus seulement pendant cette acquisition ciblée. Une unique
   destination ACSM exacte est préférée aux autres URL admises ; deux ACSM
   distincts sont refusés. Aucun endpoint de commande n'est deviné ni chargé
   automatiquement depuis l'inventaire. Un formulaire
   de connexion HTML 200, un HTML d'erreur, zéro lien, plusieurs liens,
   HTTP 410, HTTP 429 et XML non ACSM ont maintenant des codes de sortie
   distincts et non sensibles.
6. Les ACSM reçus et validés sont uniquement confiés à
   `acquisition_broker.import_acsm` avec l'identité propriétaire contrôlée
   par `identity_broker`. Aucun outil DRM interne nouveau, aucune
   acquisition planifiée Numilog (#1862).

## Diagnostic sûr pour lever l'incertitude

Événements déjà prévus dans les diagnostics du plugin :

- `bibliothèque livraison` : phase `owned_card`,
  `page_kind=meslivres`, pagination et **quatre compteurs numériques bornés** :
  `cards`, `js_shapes_in_cards`, `js_shapes_outside_cards`,
  `bound_links`. Le diagnostic ne dit rien des valeurs privées.
- `livraison ACSM` : origine de la décision `owned_card` ou `readbook`.
- `livraison HTML` : `page_kind=readbook/meslivres/delivery_intermediate`,
  catégorie `login/error/intermediate/invalid`, code
  `session_expired/html_error/missing/ambiguous/single_allowed_candidate`
  et, pour les pages HTML intermédiaires parsables, uniquement
  `js_delivery_shapes`, `absolute_html_hrefs`,
  `absolute_script_literals`, `allowed_candidates`,
  `attested_acsm_candidates` (entiers bornés).
  Pas de chemin, query string, cookie, titre, `ProductId`,
  `IndexExemplaire` ni document HTML.

Interprétation **sans présumer le comportement du site réel** :

- `js_shapes_outside_cards>0` avec `bound_links=0` : l'objet est
  détectable mais la page privée ne prouve **pas** sa correspondance avec
  un livre acheté précis. Aucun GET direct depuis cet objet.
- `js_shapes_in_cards>0`, `bound_links=0` : un objet a été vu dans une
  fiche, mais l'association, le format, l'hôte, le port, le chemin,
  la cardinalité des liens ou les marqueurs sont insuffisants.
- `bound_links>0` puis échec HTTP : une preuve structurée a été retenue,
  mais aucun succès ACSM/ADEPT ne peut être déduit du compteur.
- `page_kind=readbook`, `js_delivery_shapes>0` et
  `attested_acsm_candidates=1` : le parcours intermédiaire avait **une
  destination ACSM attestée**, même si des liens de navigation Numilog
  étaient également présents. Le lien signé est alors prioritaire sur
  les autres URL admises ; plusieurs ACSM restent **ambiguës** et sont
  refusées. Sans ACSM, une seule destination générique conserve le
  parcours EPUB historique.
- `allowed_candidates>1`, `attested_acsm_candidates=0` : la page
  intermédiaire n'a pas de destination unique prouvée ; elle est rejetée.
- `page_kind=readbook`, `allowed_candidates=0` : le HTML réellement
  reçu ne fournit pas de destination sûre identifiable par le parseur.
  Ce **n'est pas** une preuve de session expirée sans marqueur de login.

Un tableau HTML avec plusieurs livres (`tbody/tr/td`) est désormais découpé
en fiches distinctes avant d'associer titres, liens et objets JavaScript.
Toute ligne multlivre impossible à découper est refusée, plutôt que
d'associer le titre du premier livre au lien du second.
La validation d'identité et du broker ACSM est appliquée **avant le GET
signé également lors des redirections 302/HTML**. L'origine de livraison
n'autorise pas un chemin autre que `/fulfillment/URLLink.acsm`.

Pour déterminer la différence avec la page réelle, ne collecter
**que** : catégorie de page (MesLivres/fiche/readBook), support de l'objet
(script/attribut), **présence sans valeurs** des attributs de corrélation,
comptes de candidats, suites de statuts HTTP/MIME **sans URL**, et état
de connexion. Ni HTML privé brut, ni URL signée, ni jeton, ni identifiant
de commande ne doivent entrer dans les logs ou artefacts CI.

## Diagnostic local d'une page HTML privée (sans aucun HTTP)

Pour qualifier précisément l'origine du JS, le propriétaire peut analyser
**localement**, sans l'envoyer à GitHub, un HTML enregistré depuis la page
qu'il a effectivement consultée :

```bash
python scripts/numilog_html_shape.py --context book-detail /chemin/local/page-privee.html
```

Choisir `--context` parmi `meslivres`, `book-detail`, `readbook`,
`confirmation`, `unknown`. Ce contexte est **déclaré par l'utilisateur**,
non inféré du nom de fichier ou du contenu secret.

L'outil ne dépend que de Python et du parseur HTML local du plugin ; il ne
récupère **aucun cookie**, ne déclenche **aucun GET**, n'exécute **aucun JS**,
ne crée **aucun fichier** et limite l'entrée à 4 Mio. Sortie stdout : JSON
avec contexte de page déclaré, nombre de `myTable`, scripts, objets de la
forme attendue, actions de téléchargement, fiches découpées et liens
rattachés. **Aucun** titre, identifiant, `ProductId`, `lid`, `auth`,
`orderid`, URL, chemin local, nom de fichier ou fragment de HTML ne sort.

Si `declared_context=confirmation` ou `book-detail` avec
`js_delivery_shapes>0` mais `library_table_count=0`, cela prouve
uniquement que l'objet est sur **cette page déclarée**, pas qu'il est
retrouvable via `MesLivres.aspx` : ne pas ajouter de nouvel endpoint
d'acquisition ni supposer une relation `ProductId == lid` sans attestation
supplémentaire. Un rapport `invalid_or_ambiguous` n'expose pas la cause
HTML brute ; conserver le refus.

Le même raisonnement s'applique à un HTML intermédiaire contenant
plusieurs URL d'origines autorisées : une destination HTTPS
`entrepot2r.numilog.com:443/fulfillment/URLLink.acsm` **unique et signée**
est sélectionnée plutôt qu'un lien générique vers le compte, le CSS ou
l'accueil. La présence de **plusieurs signatures ACSM distinctes** arrête
tout téléchargement ; aucun choix arbitraire par premier candidat.

Le propriétaire peut transmettre **uniquement ce petit JSON expurgé** au
ticket #1883. Conserver le HTML brut et les URLs ACSM signées hors de toute
issue, fixture, CI, capture et dépôt Git ; supprimer la copie privée lorsque
son diagnostic n'est plus nécessaire.

## Validation et limites

Les fixtures valident aussi les objets JS à clés non citées et la forme
JSON à propriétés entre guillemets, sans exécution de script.

Les fixtures utilisent des comptes, numéros, signatures et réponses
**entièrement fictifs** avec `httpx.MockTransport`. Elles testent deux livres
achetés différents, une référence non possédée, les liens orphelins,
ambiguïtés, HTTP 403/410/429, redirections hôte/port interdits, XML trompeur,
login HTML 200, import ACSM unique, action ADEPT requise et suppression
des temporaires. Elles ne contactent pas Numilog.

La page réellement fournie a désormais été confrontée aux deux parseurs
hors réseau : le défaut d'ambiguïté est reproduit et sa correction vérifiée.
Les assertions de transport restent fictives. Un essai terrain de la version
corrigée peut vérifier la session et le fulfillment, sans être présenté comme
un succès déjà obtenu ni imposé comme recette manuelle implicite de clôture.
Une observation nouvelle peut conduire à rouvrir #1883 ou à qualifier une
étape distincte avec des diagnostics expurgés. Aucun HTML privé, cookie,
identifiant réel ou lien signé du document joint n'est archivé dans le dépôt.
