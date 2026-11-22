# P0 Notices — #3724 : couverture des producteurs et intentions

Inventaire statique du dépôt au 20 novembre 2026, réceptions actualisées le 22 novembre 2026. Il décrit les manifestes
`provider.library` distribués sous `src/plugins`, non les plugins effectivement
activés sur une installation. Aucune requête fournisseur ni inspection de
configuration privée. Les capacités de catalogue, prêt et téléchargement
ne sont pas des capacités d'enrichissement documentaire.

## Matrice de réception

- **Partiel** : port ou chemin de données observé dans le code, avec une
  limite de réception indiquée. **Non attesté** : aucune preuve pour le port
  générique d'enrichissement, donc aucun droit d'exécution implicite.
- **Non applicable** : le déclencheur n'est pas déclaré dans le manifeste ;
  cela ne signifie pas que toutes les données bibliographiques sont complètes.
- Les recettes contrôlées liées ci-dessous sont distinctes de preuves réseau
  réelles. « Par défaut » ne vaut pas activation ni consentement.
- Achat Numilog : port spécialisé reçu (#1072). Emprunt existant : port
  Core de réparation cache-only reçu (#7485), sous conditions ci-dessous.
  Admission commune achat/catalogue (#7464) reçue (CI 44993027479,
  AST 44993027430, installation 44993027427 SUCCESS ; squash caed0d56).
  Le raccordement du port prêt à cette admission est proposé dans le présent
  delta, à recevoir après CI ; import local et fetch documentaire non qualifié
  restent sans port positif.

| Provider | Observation / association disponible | Statut et reste à qualifier |
| --- | --- | --- |
| `provider.baen` | Recherche/détail et offre téléchargement, singleton public désactivé par défaut. | Partiel : Free Library JSON anonyme → C durable, #7237 ; requête q partielle sans admission C, format EPUB non attesté, offre/action et enrichissement non reçus. |
| `provider.bnf-pret` | Recherche/détail, sync prêts, emprunt ; désactivé par défaut. | Partiel : Search authentifié même appel → C self privée opaque, #7250 ; GTK contrôlée #7266. Association prêt existant → édition publique spécifique BnF-Prêt toujours non attestée ; port cache-only Core conditionnel #7485, sans activation fournisseur constatée. Un résultat de recherche ne constitue pas un prêt. |
| `provider.biblioaccess.la-rochelle` | Sync prêts et téléchargement, désactivé par défaut. | Non attesté : lien exact prêt/notice catalogue entre les deux ports ; pas de rapprochement par titre seul. |
| `provider.biblioaccess-catalogue.la-rochelle` | Catalogue singleton public, déclaration même appel depuis ancre titre/URL, #7049. | Partiel : recette C SQLite contrôlée ; zéro attesté propagé dans les trois parcours #7259/#7285. Lien avec prêt privé et port d'enrichissement non attestés. |
| `provider.paris` | Recherche et détail Syracuse, emprunt et sync prêts ; carte requise. #7117 et #7118. | Partiel : séries et déclaration anonyme contrôlées, chemin anonyme non activé au runtime ; prêt→C Paris et GetRecord non attestés ; port cache-only Core conditionnel #7485, sans preuve terrain Paris. |
| `provider.reference` | Téléchargement déclaré, désactivé par défaut. | Catalogue et sync non applicables selon manifeste ; enrichissement non attesté. |
| `provider.numilog` | Inventaire privé, achat existant et association publique dans `purchase_enrichment_observation` ; recherche/détail. | Partiel : #1072, chemin achat A après commit et port spécialisé ; preuve terrain et admission générique à qualifier. |
| `provider.numilog.catalog` | Catalogue anonyme singleton désactivé par défaut ; réponse typée exacte, détail public, cache de parsing. | Partiel : #1886 reçu, GET publics équivalents simultanés mutualisés #7262, layout Ra contrôlé hors réseau #7269/#7275/#7287, garde brSimple #7294. Refus terrain initiaux #7163 non individualisés ; #1072 reste distinct. |
| `provider.bnf-catalogue` | Singleton public SRU ; déclaration même appel, preuve ISBN/âge et support bornée. | Partiel : recette source C contrôlée ; qualification #4530 reçue (matrice attesté/unknown/unverified ; aucune preuve SRU structurée numérique déclarée acquise), pas de port d'enrichissement générique attesté. |
| `provider.ebooksgratuits` | Singleton public OPDS ; recherche/détail et téléchargement. | Partiel : port documentaire OPDS canonique #7164 ; chemin numérique qualifié, offre legacy séparée. Pas de restauration d'action depuis C, UUID/alias et autres formats non attestés. |
| `provider.kobo` | Recherche, détail, catalogue compte/vault et téléchargement ; déclaration publique distincte sous garde dans session privée. | Partiel : preuve bibliographique publique déclarative, pas de témoin anonyme C automatique ; port achat/association exacte et politique à qualifier, aucun accès Vault supplémentaire. |
| `provider.gallica` | Recherche/détail et téléchargement, bundle audio déclaré. | Partiel : port documentaire OPDS anonyme #7144 ; EPUB/PDF exact ARK/MIME, offre legacy séparée ; audio SRU non qualifié pour C et pas de restauration d'action depuis C. |
| `provider.gutenberg` | Singleton public OPDS, contact requis, `search` liste ; langue dcterms et date issued explicites, offre EPUB distincte. | Partiel : déclaration même appel anonyme #7130/#7139, format EPUB #7141, port legacy distinct ; action d'offre depuis C non reçue ; pas de couverture ou date d'une autre édition. |

Sources internes : manifestes sous `src/plugins/*/cifereca-plugin.json` ;
`provider-biblioaccess/cifereca_biblioaccess_catalogue.py`,
`provider-numilog/cifereca_numilog_public_provider.py`,
`provider-numilog/cifereca_numilog.py`,
`provider-bnf-catalogue/cifereca_bnf_catalogue.py`,
`provider-paris/cifereca_paris.py`, `provider-kobo/cifereca_kobo.py`,
`provider-gutenberg/cifereca_gutenberg.py`. Liens de réception :
[#7049](https://github.com/pzia/cifereca/pull/7049),
[#7117](https://github.com/pzia/cifereca/pull/7117),
[#7118](https://github.com/pzia/cifereca/pull/7118),
[#1886](https://github.com/pzia/cifereca/issues/1886),
[#4530](https://github.com/pzia/cifereca/issues/4530),
[#1072](https://github.com/pzia/cifereca/issues/1072).

## Lacune contractuelle prioritaire : occurrence catalogue et offre

Le contrat `PublicSearchNotice` v1 autorise une déclaration distincte pour
une projection publique. Dans `public_search_projection.py`, la déclaration
doit être sans compte, ownership, téléchargement ni URL de livraison.
`search_orchestration._public_rows_from_response` exige en plus pour le
témoin d'admission durable anonyme une session `__public__`, credentials
et comptes vides, collection `public_catalog`, et exactement une déclaration
pointant sur **le même objet** résultat, sans disponibilité de téléchargement.

Exemple vérifié dans le code Gutenberg : `_parse_atom_entries` produit un
résultat avec `source_collection="Project Gutenberg"`, `downloadable=True`
et éventuellement une URL EPUB. Changer ce résultat en notice en lecture
seule détruirait une offre historique ; copier seulement sa bibliographie
dans une déclaration distincte ne donne pas le témoin durable. Il ne faut
pas relâcher la garde Core existante pour faire passer ce résultat.

### Réceptions partielles au 21/11 : port documentaire distinct

Les producteurs suivants réutilisent les parseurs et les ports existants.
Le port documentaire retourne l'objet exact sans autorité de livraison ;
le parcours legacy reste disponible dans le provider. Cette séparation
ne prouve **pas** que la ligne C permet déjà de récupérer l'offre dans
l'interface. Aucun nouveau GET au seul rendu, aucun accès nouveau,
aucune généralisation du format ou de la couverture.

| Admission contrôlée | PR fusionnée / commit squash | CI au HEAD exact |
| --- | --- | --- |
| Gutenberg OPDS, un ID Atom exact | [#7130](https://github.com/pzia/cifereca/pull/7130), `f247ef91` | [44801145285](https://github.com/pzia/cifereca/actions/runs/44801145285) success |
| Gallica OPDS, ARK et lien MIME exact | [#7144](https://github.com/pzia/cifereca/pull/7144), `250d1d2d` | [44807735766](https://github.com/pzia/cifereca/actions/runs/44807735766) success |
| EbooksGratuits OPDS canonique | [#7164](https://github.com/pzia/cifereca/pull/7164), `22b0b725` | [44817039919](https://github.com/pzia/cifereca/actions/runs/44817039919) success |
| Baen Free Library JSON | [#7237](https://github.com/pzia/cifereca/pull/7237), `dd1cc345` | [44873194073](https://github.com/pzia/cifereca/actions/runs/44873194073) success |
| BnF-Prêt privé, sans promotion publique | [#7250](https://github.com/pzia/cifereca/pull/7250), `6ae3bc38` | [44876027438](https://github.com/pzia/cifereca/actions/runs/44876027438), AST/install/E2E success |
| BiblioAccess zéro terminal, pas un refus | [#7285](https://github.com/pzia/cifereca/pull/7285), `22f2838c` | [44884879715](https://github.com/pzia/cifereca/actions/runs/44884879715), AST/install/E2E success |
| Numilog GET équivalents en vol | [#7262](https://github.com/pzia/cifereca/pull/7262), `35fc271f` | [44880414504](https://github.com/pzia/cifereca/actions/runs/44880414504) success |

Recettes synthétiques avec transport contrôlé, Storage/SQLite, éviction
et redémarrage selon le périmètre propre à chaque PR ; ne pas les présenter
comme une observation réseau réelle. BnF-Prêt #7250 est **privé**, avec
session/compte/révision et relation self ; aucune jointure forte entre
producteurs n'est inférée. La recette GTK #7266 (squash `4eb696c8`,
[CI](https://github.com/pzia/cifereca/actions/runs/44886360567) /
[E2E](https://github.com/pzia/cifereca/actions/runs/44886360467) success)
prouve le menu privé 0/1/N en données contrôlées, pas l'association à un
prêt ni une preuve supplémentaire de fournisseur.

Reste à recevoir : action contextuelle sur une C exacte sans inventer de
droit (coordination #3685/#4988), relation forte multi-source effectivement
attestée (#2385/#4552), qualification des producteurs partiels et
reliquats d'enrichissement générique ci-dessous. Ne pas refaire les ports documentaires
déjà reçus ni activer un fournisseur pour obtenir un PASS.

## Intentions génériques : garde par déclencheur

| Déclencheur | Preuve minimale avant admission, à réattester à l'exécution |
| --- | --- |
| Achat existant | Réutiliser `PurchaseEnrichmentCandidate/Observation` et A active après commit, exactes ; aucun achat déclenché. |
| Emprunt existant | Observation positive native du prêt, compte/emprunteur/session/scope et édition exacts ; révocation/expiration refusent, pas de nouvel emprunt. |
| Import local | Artifact et Placement exacts déjà attestés, révision du fichier et scope de lecture ; pas d'acquisition, déplacement ni transformation. |
| Catalogue à compléter | Source/version/payload attestés et politique de lecture exacte ; pas d'A inventée, ni GET automatique sur l'ensemble du catalogue. |

### Réception partielle du port emprunt — 22/11

[#7485](https://github.com/pzia/cifereca/pull/7485), HEAD
`c998e2f354faab551af397d39b0f86a2725d947a`, squash
`ef9e8e263b92ca3d696a83b7695dd5618b34efb7` : PR CI 44992305605,
AST 44992305609, installation 44992305578, E2E 44992305615 SUCCESS.
Trois blobs HEAD/squash/main identiques. `notice_c_loan_enrichment.py`
réutilise le vrai inventaire natif et le journal existant : prêt actif,
compte/epoch/branche et édition attestés, observation publique Search v2
indépendamment admise, titre/auteur uniquement comme veto, réparation d'une
self-C manquante. Tests SQLite contrôlés de déduplication/révocation ;
aucun GET, nouvelle permission, copie privée, création d'Artifact ou
mutation fournisseur. La disponibilité du port Core ne prouve pas qu'un
provider précis fournit ces coordonnées en production. Aucun verdict
terrain supplémentaire.

Le journal #4982 demeure le seul pending store ; #6939 possède le cycle
runtime. Les preuves privées ne doivent pas persister en clair dans les jobs.
À défaut de port qualifié, résultat unsupported et aucune admission.
L'import local n'est pas un provider.library. Les liens existants dans
`manual_import_display_admission.py`, `observed_import_provenance.py` et
`local_import_display_links.py` ne sont que des preuves locales ou des
indices d'affichage ; ils ne prouvent ni une édition distante ni un droit.
L'admission générique refuse explicitement le déclencheur import non qualifié
(`test_core_p0_3724_enrichment_intent.py`, y compris après restart). Aucun port
positif sans preuve admissible, aucun faux fournisseur.
La recette devra couvrir 0/1/N, ABA/révocation, retry/restart, absence
de droits/placement nouveaux, parsing et GET non dupliqués.

Cette matrice est une réception d'inventaire statique, pas une preuve
d'activation ni une clôture de #3724.
