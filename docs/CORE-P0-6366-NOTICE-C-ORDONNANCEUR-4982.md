# #6366 / #4982 — contrat de livraison de l’ordonnanceur Notice C

Statut : préparation du lot réactivé P-1, **pas d’activation runtime**.
Base examinée : `19670ebd`. Le prérequis est la réception minimale des six vues
physiques puis l’ABI/persistance vérifiées. Ce contrat ne crée ni file parallèle,
ni worker, ni table, ni nouveau chemin réseau.

## 1. Ports et prérequis vérifiés

| Port existant à réutiliser | Capacité observée | Limite à lever avant activation |
|---|---|---|
| `core/notice_source_observation.py` et `notice_parse_witness.py` | Observation admise, clé exacte, parseur/version, JSON canonique, révision | Le JSON de l’observation peut être une projection et non le corps fournisseur ; preuve d’input reparsable nécessaire |
| `notice_cover_repository.py` | Lecture exacte indexée ; `persist_notice_cover[_many]` ; CAS d’epoch, watermark, transaction SQLite | Exposer un jeton de publication monotone survivant à l’éviction ABA ; distinguer version de parseur, version d’input et fraîcheur |
| `notice_c_cover_index.py` | Lecture exacte ; CAS C par `persist_consolidated_cover_source`, groupe OriginLink atomique extensible | CAS du jeu complet des dépendances et de la génération du consolidateur ; ne pas remplacer le lien fort existant |
| `notice_c_origin_link_producer.py` | Producteur Core de rapprochements à preuve OriginLink, refus des collisions | Pas de réconciliation aveugle à partir d’ID/ISBN ; relire les preuves au commit |
| `task_runtime.TaskBrokerImpl` / `task_queue.PriorityExecutor` | Exécuteur bornable, annulation coopérative, priorités, admission non bloquante | Reprendre l’instance Core et son budget ; le pool n’est pas un journal durable |
| `task_admission.SharedTaskAdmission` | Réservation utilisateur, plafond global/par scope, compteurs agrégés | Contrôle process-local, opt-in ; vérifier le câblage réel, non un quota interprocessus |
| `gui/document_cover_service.py` | Lecture source cache-only, projection N2, invalidations et garde des callbacks | Notification ciblée après COMMIT ; aucun accès réseau ajouté par une lecture |

Point d’arrêt : ne pas déclarer `reparse_cached_input` disponible si seuls les
champs projetés survivent, ni `fetch_source_notice` disponible si le producteur ne
déclare pas une lecture exacte autorisée. Les parseurs restent détenus par #3724.
L’ABI attendue doit être contrôlée pour chaque producteur, pas inventée par Core.

## 2. Identité des travaux, références plutôt que copies

Quatre types fermés : `reparse_cached_input`, `relink_notices`,
`recompute_aggregate`, `fetch_source_notice`.

Une clé logique est (type, cible exacte, scope de visibilité). Une clé de
tentative ajoute les versions/digests d’input, version parseur ou consolidateur,
génération de demande et vecteur ordonné des dépendances. Une identité
spectatrice ne change pas une cible publique. Une cible privée porte
l’identité propriétaire, fournisseur, branche, credential/account exacts et
lease de source. Les références de sources sont réutilisées, jamais des secrets,
URL temporaires, octets EPUB, images, copies de bibliographies ou closures.

Une répétition exacte retourne la tentative courante. Une version plus récente
remplace la demande logique, accroît sa génération, marque l’ancien travail
stale et annule son handle si possible. Retour A→B→A : une nouvelle génération
reste obligatoire malgré le même digest. Un changement d’epoch de compte/source
révoque l’ancienne autorité privée ; le scheduler ne reconstruit jamais une
session depuis un ID stocké.

Journal minimal de travaux dans la **même** Storage/transaction SQLite ; choix
physique à arbitrer avec #2385 (métadonnées préfixées existantes ou index dédié de
travaux dans la base canonique, jamais un autre stockage de notices). Le journal
ne possède que références, versions, statut, tentative, lease/fencing, échéance
et motif fermé, aucun corps documentaire. Ne pas réutiliser un entier epoch de
ligne supprimée comme preuve suffisante de fraîcheur. Migration additive,
bornée, réexécutable ; aucun effacement de données existantes.

## 3. Graphe et transitions

- Reparse : cache exact admis + version de parseur déclarée → observation
  validée ; jamais un fetch implicite quand l’input n’est plus présent.
- Relink : observations courantes + preuves fortes autorisées → relation ; jamais
  une recherche par ressemblance ou un droit.
- Recompute : relation + vecteur de toutes observations/versions → attributs de
  C ; conserver champs et alternatives indépendantes, N1 prioritaire.
- Fetch : déclencheur et consentement/politique existants, session/lease exacte,
  voie transport déjà autorisée ; pas d’ajout d’endpoint, GET de découverte
  implicite, décision GTK, POST ni renouvellement de droit.

Les dépendances sont DAG sur générations exactes. Une dépendance pending
n’occupe pas de worker ; une dépendance remplacée rend le résultat stale. Un
cycle, un membre inconnu ou une génération invalide produit refus fermé.
Les travaux dédupliqués peuvent partager une dépendance déjà current.

| État durable | Transition admissible |
|---|---|
| pending | Claim atomique avec lease de tentative, exécution hors transaction ; report si admission pleine |
| current | Commit atomique des écritures canoniques et du succès, générations et lease exacts ; même input = idempotent |
| stale | Input, preuve ou autorité révoqués ; aucun résultat tardif ne repasse current, nouvelle génération si autorisée |
| error | Motif fermé ; retry borné sur erreurs transitoires, permanent sinon ; conserver dernière bonne C |

Le lease d’exécution est un champ de pending, non une preuve documentaire.
Prise de lease et libération idempotentes ; fencing monotone même après crash.
Toute publication doit relire atomiquement : cible, génération, lease,
versions/dépendances complètes, qualité des observations, autorité privée.
Utiliser les writers transactionnels existants (dont
`_persist_notice_cover_txn` et `_persist_consolidated_cover_source_txn`) plutôt
qu’un UPDATE SQL contournant leur validation. Un succès d’écriture C sans
validation du journal dans la même transaction n’est pas un succès livré.
N’émettre une notification de révision qu’après commit ; un replay de notification
est inoffensif. Une notification perdue est réparée par relecture du même index,
pas par un fetch.

Les transactions sont courtes ; lecture input, parsing, preuve, transport et
attentes sont hors lock SQLite. Une observation tardive partielle ne doit pas
effacer le dernier état complet, ni avancer fictivement l’epoch de couverture.
Une erreur n’est pas `missing`. Une suppression d’input n’efface pas la C
durable ni un fichier local. Une photo N3 en cache n’est pas une preuve C.

## 4. Budget proposé (à valider avant raccordement)

Régulateur Core uniquement, soumission non bloquante via TaskBrokerImpl et
SharedTaskAdmission existants ; pas de thread pool spécifique Notice C. Un
claim ne réserve pas indéfiniment une capacité. S’il ne peut être soumis,
libérer son lease et conserver pending. Limites proposées pour premier test :
32 candidats examinés/tick, 2 tâches Notice C en vol, 1 par scope privé,
3 tentatives automatiques transitoires, délais 1 s/4 s/16 s avec horloge
injectable et jitter borné en production. Ces valeurs sont des paramètres de
recette, pas des garanties SLA ni des réglages de production actés.

Travaux CPU/SQLite aux priorités METADATA/MAINTENANCE ; ne pas contourner
l’admission utilisateur avec USER_ACTION pour un réchauffement. Round-robin par
scope éligible et âge dans une classe ; promotion de maintenance plafonnée à
METADATA après attente bornée, sans franchir les lanes interactives. Une
saturation de tous les slots par du travail autorisé ne promet pas de délai
absolu : dès disponibilité d’un slot de fond, aucun scope éligible ne doit être
affamé par un autre. Les retries d’un fournisseur ne bloquent pas les autres ;
pas de sleep dans un worker. Refuser/retarder l’excédent, sans croissance du heap
ni journal non borné. Pas de scan récurrent global des notices connu de GTK.

## 5. Matrice de réception à réaliser (non encore exécutée)

| Cas contrôlé | Preuve minimale |
|---|---|
| 100 demandes simultanées identiques | 1 claim, 1 exécution, 1 commit, les autres dédupliquées |
| Parser v1→v2/retour v1, input A→B→A | Garde d’input et génération, ancien worker refuse le commit même si digest identique |
| Relation en cours, source concurrente changée | Relecture de toutes dépendances, rollback entier, aucune seconde relation |
| Crash avant claim/après claim/pendant calcul/avant commit/après commit | Reprise du journal sans perte et sans double publication ; fencing de l’ancien runner |
| Input source expiré / C conservée | Cache MISS = unknown ; pas de GET ni de suppression de C |
| Broker saturé / navigation rapide | Retour immédiat côté GTK ; réserve user intacte ; annulation/reprise contrôlées |
| Scopes publics A/B/C et deux comptes privés A1/A2 | Dédup publique et isolation privée ; révoquer A1 sans affecter A2/C |
| Change de compte A1→A2→A1 avant fetch/commit | Ancienne lease refusée avant dispatch et commit ; zéro GET après révocation |
| Offline/429/503/refus d’accès/parse invalide | Retry borné sur transitoire seulement ; error fermé, dernier current lisible |
| Burst prolongé de travaux d’un scope | Équité sur slots de fond disponibles, backlog et mémoire bornés |
| Dépendance absente/cycle/ancienne génération | Refus ou attente sans worker bloqué ; aucun résultat inventé |
| C de 2→3 sources, révision ciblée, homonyme | Même C forte et seul N2 concerné invalidé ; N3 commun intact |
| 6 vues liste/détail avant/après restart et offline | N4 correct, aucun GET implicite du menu/rendu, aucune mutation de droit |
| Corruption/migration/rollback SQLite | Refus fermé, reprise et intégrité ; aucune perte du seul EPUB |

Mesurer en tests/diagnostics uniquement des nombres et codes fermés :
demandes/claims/dedup/stale/retries/refus d’admission par type, latence file
bornée, coût parsing, commit/invalidation, GET autorisés, MISS/HIT N2/N3.
Aucun identifiant de compte, URL signée, réponse fournisseur, chemin de fichier
ou exception brute publié. Captures GTK synthétiques selon politique E2E.

## 6. Ordre de livraison et gates

Après réception minimale validée : (A) ABI input/révisions et journal CAS,
(B) ordonnanceur inerte sur fixtures puis worker reparse/cache-only,
(C) relink/recompute sur preuves et writers existants, (D) fetch uniquement
par port déclaré et consentement actuel, (E) concurrence/reprise/stress et
réception six vues après intégration. Pas de second ordonnanceur. Chaque PR
référence #4982 et #6366, SHA exact et toutes gates applicables ; aucun merge
avec gate pending, aucun changement de workflow nécessaire. Les cas positifs
de D/E restent bloqués tant que leurs prérequis ne sont pas démontrés.
