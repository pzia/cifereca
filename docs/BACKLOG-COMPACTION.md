# Consolidation et capitalisation — #8195

Le dernier arbitrage propriétaire dissocie le transfert déterministe à la clôture
d'un P0 et la capitalisation sémantique différée à la demande. Aucun moteur LLM
dans CI/CD, aucun chiffrement du staging. Voir [politique commune](AGENT-DELIVERY-POLICY.md#consolidation-à-la-clôture-dun-p0).

## Qualification et limites

Focus/Backlog atteste le corpus public clos dans le corps du P0, avant fermeture
completed sous pzia, par une seule ligne :

`<!-- backlog-staging-v1: 123,456 ; public -->`

Les nombres constituent le rattachement explicite au maître : toute issue fermée,
quelle que soit sa priorité (ou son absence) et son motif de clôture, est admissible.
`none` pour un P0 autonome. Aucun changement de priorité, motif ou relation native.
Il ne s'agit pas d'un plan de synthèse. Ne jamais inférer les cibles de simples
mentions #n ; aucun lien retour dans le ticket historique n'est requis.
Les relations natives doivent être compatibles avec l'inventaire. Refuser les
corpus déjà transférés (journal ou renvoi de compactage).
Aucun autre agent ne modifie le corpus pendant la clôture. Le pilote reste
borné à vingt issues (maître inclus), 8 Mio de manifeste ; au-delà, refus sans mutation.

## Staging en clair

Une référence orpheline par P0, `staging/backlog/p0-N`, sans parent ni fusion
dans main ; manifeste JSON canonique : corps et commentaires intégraux,
identifiants, auteurs publics, dates, références, labels, relations natives.
Pas de dump REST, email, secret, capture privée ou copie des pièces jointes.
Le contrôle heuristique de secrets ne garantit pas une détection exhaustive.
La déclaration de publicité est obligatoire. Pas de chiffrement.

La suppression de la ref ne garantit pas la purge physique des objets GitHub,
ni des copies externes. Le gain logique et le gain physique sont distincts.
Le contenu ne doit pas être publié dans les logs, artefacts ou historique main.

## Exécution et réception

Le workflow dédié, sur fermeture completed pzia et première tentative,
publie le manifeste dans une branche orpheline via Git avec bail
d'absence ; relecture depuis un dépôt vide avant toute mutation.
Ne jamais merger le staging. Les deux nettoyeurs de branches excluent
ce namespace, y compris les suppressions forcées.

Le journal START (SHA-256 et commit) précède les mutations. Les textes d'au moins 256 octets réellement raccourcissables sont remplacés par des renvois déterministes
au P0 en conservant les références explicites, les identifiants des
commentaires et les métadonnées. Aucun commentaire ni issue supprimé.
Parcours standard : tranches d'au plus dix mutations, dimensionnées de façon déterministe selon le nombre d'issues et de pages de commentaires (taille inscrite dans le manifeste), avec PROGRESS et continuation explicite selon le protocole ci-dessous ; gain contextuel positif
après START et DONE. Vérifier main, le corpus et le staging avant chaque
mutation, relire chaque résultat. Toute erreur arrête sans reprise
automatique ; garder le staging, y compris après échec partiel.
Fenêtre REST non atomique ; gel procédural du corpus obligatoire.
Expiration 24 h, aucun rerun. Le dispatch manuel est lecture seule.
Après une écriture vérifiée, une progression de l'horodatage de l'issue
seule est admise ; toute autre divergence arrête. Si seuls les journaux
de fin manquent et que toutes les mutations sont déjà effectives, Backlog
peut fermer sous pzia un contrôle sans label ni assignation nommé
`Réception consolidation #8195 — N` avec JSON exact :

```json
{"version":1,"p0":123,"stage_sha":"<commit>","manifest_sha256":"<sha256>","main_sha":"<main>","reviewed":true}
```

La réception relit le manifeste, vérifie les textes attendus, la provenance
publique et les relations ; elle n'écrit que DONE. Elle refuse un corpus
incomplet, un START absent ou un rejeu. La revue Backlog couvre les
métadonnées non présentes dans le manifeste. Aucun nettoyage de reprise.

## Nouvelle consolidation après refus avant mutation

Après preuve Backlog d'un refus avant toute mutation, absence de ref et de
journaux, geler le corpus public déjà fermé et lancer le dispatch **lecture seule** du workflow avec `issues: recovery:N`.
Il renvoie le SHA-256 du manifeste canonique sur main exact, les numéros,
la taille du staging, le nombre de mutations et la taille de tranche, jamais
les textes. Ne pas modifier le corpus entre cette prévisualisation et la clôture
du contrôle. Le quota REST partagé et les latences peuvent imposer un arrêt
sûr et une nouvelle continuation ; ne pas relancer un run.
Fermer un nouveau contrôle propriétaire sans label ni assignation, titre
`Nouvelle consolidation #8195 — N`, JSON exact :

```json
{"version":1,"p0":123,"main_sha":"<main>","manifest_sha256":"<sha256>","unstarted_review":true}
```

La revue explicite atteste le refus initial et l'absence de mutation ; le CLI
vérifie l'absence de staging, de renvois et de journaux, le manifeste exact,
le contrôle et main. Nouvelle fenêtre de 24 h liée au contrôle, pas de rerun
ni réouverture. Exclusion des lots historiques et #8207. Toute dérive refuse.
Les anciens manifestes standard sans `standard_batches` conservent leurs
journaux historiques sans PROGRESS.

## Capitalisation différée

Sur demande, les LLM des rôles propriétaires inventorient
`staging/backlog/p0-*`, lisent les manifestes, vérifient les empreintes
et mutualisent les connaissances dans les documents/skills canoniques.
La capitalisation est une obligation de moyens : conserver les connaissances
utiles, sans couverture exhaustive. Le propriétaire accepte la perte résiduelle
d'informations historiques non capitalisées après revue raisonnable. Backlog
réceptionne cette revue, puis ferme completed sous
pzia un contrôle sans label ni assignation, titre `Capitalisation #8195 — N`,
corps JSON :

```json
{"version":1,"p0":123,"stage_sha":"<commit>","manifest_sha256":"<sha256>","main_sha":"<main>","evidence":[{"path":"AGENTS.md","sha256":"<sha256>"}],"semantic_review":true}
```

Le workflow vérifie le contrôle, DONE, le manifeste et les documents
sur main. La revue sémantique est déclarative, pas une preuve par le CLI.
Journal PURGE-START persistant avant suppression de la ref avec bail sur le
commit exact ; journal PURGED seulement après relecture de l'absence de ref.
Pas de purge physique garantie. Si échec, inventaire et reprise
explicite par CI/CD, jamais rerun. Aucun chiffrement, aucune clé.

Distinguer clôture fonctionnelle, consolidation, capitalisation et purge.
Mesurer caractères, octets et tokens estimés ; inclure staging, nouvelles
synthèses, documents et journaux dans le gain global. Recette synthétique avant toute déclaration de périmètre historique,
puis pilote réel, exact HEAD ; pas de réception globale avant preuve des deux
parcours. Le stockage physique GitHub reste hors garantie.

## Lots historiques sous un maître unique

Créer des contrôles ouverts sans priorité ni assignation, titre `Lot consolidation #8195 — M`.
Chaque contrôle L porte exactement `<!-- backlog-lot-v1: M ; A,B ; public -->` (un ou deux membres).
Le maître P0 M porte une ligne `<!-- backlog-master-lot-v1: L ; A,B ; public -->` par lot,
sans marqueur de clôture standard. Inventaire disjoint, contrôles et membres distincts,
au plus 100 lots. Fermer le maître completed après qualification, puis fermer chaque contrôle
completed sous pzia, séquentiellement après réception et purge du précédent. Le contrôle
est l'ancre technique de staging, journaux, réception et purge existants ; le maître reste
intact et son empreinte est vérifiée. Le corps du contrôle reste intact. Aucun changement
historique de priorité, état ou relation. Chaque lot reste borné à 8 Mio de manifeste intégral et à la pagination existante ;
le parcours standard utilise également 8 Mio. Les écritures sont fractionnées en tranches de dix,
avec un journal PROGRESS lié à l'empreinte et au curseur. Le staging intégral reste
présent jusqu'à DONE et capitalisation. Ne jamais purger un lot incomplet. Les journaux du
contrôle et les renvois des membres empêchent le rejeu ; ne pas réutiliser un contrôle.
La clôture du maître ne vaut pas réception des lots : inventorier leurs journaux.

### Poursuite standard ou historique

Après `LOT_PENDING`, fermer sous pzia un contrôle sans label ni assignation nommé
`Poursuite consolidation #8195 — N` (N = P0 standard ou contrôle de lot), avec JSON exact :

```json
{"version":1,"p0":123,"stage_sha":"<commit>","manifest_sha256":"<sha256>","main_sha":"<main>","cursor":10}
```

La reprise reconstruit le préfixe déterministe depuis le manifeste intégral, vérifie
le journal exact, tous les textes, les relations et le maître, puis exécute au plus dix
écritures nouvelles (taille effective fixée par le manifeste standard). Un ancien curseur ou un écart entraîne un refus sûr.

Après interruption d'une tranche avant son journal PROGRESS ou DONE, conserver
le staging et le gel. Backlog qualifie le préfixe déjà appliqué, puis ferme sous
pzia un nouveau contrôle de même titre, avec JSON exact (version 2) :

```json
{"version":2,"p0":123,"stage_sha":"<commit>","manifest_sha256":"<sha256>","main_sha":"<main>","cursor":0,"applied":3}
```

`cursor` est le dernier curseur journalisé (0 pour START seul) ; `applied` est
le nombre total de mutations effectivement appliquées, entre ce curseur et
la fin de sa tranche. Le workflow vérifie séparément le journal et le préfixe
intégral, le maître, les identités, les dates monotones des éléments modifiés,
les relations et les empreintes. Il ne rejoue aucune mutation : il termine
uniquement la tranche interrompue (journal seul si elle était complète).
Une divergence, un trou, une ancienne autorisation ou une mutation hors tranche
est refusé. Les métadonnées non présentes dans le manifeste restent à revoir
par Backlog. Une lecture REST instable sur le seul horodatage de l'issue peut
être relue intégralement, au plus trois scans, sans accepter d'autre dérive ;
le snapshot renvoyé reste exact, non normalisé.

DONE seulement après la dernière tranche. Aucun rerun, aucune purge avant DONE.


### Transition XL des inventaires gelés (#8207)

Sur arbitrage propriétaire, un contrôle de groupe distinct, de même titre
`Lot consolidation #8195 — M`, porte exactement :

`<!-- backlog-xl-v1: M ; L1,L2,L3,L4,L5 ; public -->`

Cinq contrôles consécutifs de l'inventaire maître (dix issues historiques)
constituent le pilote ; dix contrôles consécutifs (vingt issues historiques)
sont autorisés seulement après réception du pilote réel. Aucun changement de
l'inventaire maître ni des lots déjà consommés. Les sources doivent comporter
deux membres chacune ; le reliquat est traité par le protocole ordinaire.
Avant toute réservation, vérifier les états, les journaux, les refs, la
publicité, les relations natives et la qualification sémantique du groupe.
Un seul groupe ou lot individuel actif ; aucun autre agent ne touche le corpus.

Créer l'ancre XL ouverte, sans label ni assignation. Chaque contrôle source,
encore ouvert, sans commentaire ni staging, reçoit **un seul** commentaire
propriétaire exact `BACKLOG-XL-CLAIM v1 #X` (X = ancre XL), puis est fermé
`not_planned` sous pzia, sans modifier son corps ni les membres historiques.
Cette fermeture technique ne vaut ni consolidation ni réception. Elle ne
déclenche pas de transfert individuel. Le workflow refuse le rejeu individuel
d'un contrôle revendiqué et refuse les sources manquantes, divergentes,
déjà consolidées ou revendiquées par un autre groupe. Ne jamais retirer une
revendication pour reprendre sans arbitrage explicite et preuve de non-mutation.
Si la réservation est interrompue, conserver l'ancre et les revendications,
requalifier avant de compléter le même groupe ; ne pas créer un doublon.

Fermer l'ancre XL completed sous pzia seulement après qualification complète.
Le manifeste v1 ajoute `xl_controls` (liste ordonnée des contrôles sources),
inclut l'ancre, les contrôles revendiqués et les membres historiques (16 ou
31 snapshots) ; la limite 8 Mio et la pagination restent inchangées.
Le staging, les journaux START/PROGRESS/DONE, les tranches de dix écritures,
les reprises explicites et la capitalisation/purge sont ceux du lot ordinaire,
sur l'ancre XL. Le corps et le commentaire de revendication des sources,
tous courts, sont conservés. Le maître et les revendications sont revérifiés
avant les écritures ; aucune fusion du staging, aucun rerun.
Les contrôles de capitalisation restent explicites et liés au SHA exact de
main et aux empreintes ; revue sémantique par membre, même avec une preuve
canonique mutualisée.

Compter cinq ou dix lots d'origine comme purgés uniquement après DONE,
capitalisation, PURGED authentifié, CI SUCCESS et ref absente pour l'ancre.
Ne pas compter les fermetures not_planned des sources comme avancement.
Les tranches et la latence REST peuvent exiger des cycles supplémentaires ;
mesurer leur coût réel et le gain logique, sans promettre de purge physique.

## Clôture et capitalisation résiduelle #8207 (28 novembre 2026)

Le chantier #8207 est abandonné (not_planned), sans reprise implicite du maître
#8249. Bilan historique : 21/100 lots purgés ; le groupe #8418 reste incomplet.
Journal authentifié START + PROGRESS 10, sans DONE ni PURGED. Dernier préfixe
observé : 18/23 mutations (sondages, non réception exhaustive) ; #199, #200,
#207, #211 et #221 encore non compactées au contrôle de clôture documentaire.
Conserver la ref staging/backlog/p0-8418, commit
`1523236e24d474ab50d8d37808a0d6a7458d9843`, manifeste SHA-256
`fd175c4944366101f0b0ea0c37686e659a79b838939eae1fd37c1c8badf1e568`.
Ne pas modifier les claims, le maître, les corps historiques ou les journaux
pour simuler une réception. La synthèse métier est dans TEST-ORGANIZATION.md ;
elle ne constitue pas une autorisation de purge.

Retours d'expérience :
- Les runs 46118182813 et 46118721307 ont refusé la poursuite sur changement
  de main. Un curseur journalisé ne décrit pas nécessairement le préfixe
  physique après interruption ; réconcilier exactement les textes, identités,
  relations et empreintes avant toute reprise éventuellement autorisée.
- Les contrôles successifs et les lectures REST répétées ont un coût
  opérationnel important. Une orchestration locale pourrait réduire les
  rebonds, mais ne dispense pas de l'autorisation et des preuves de réception.
- La PR expérimentale #8432, fermée sans fusion, conserve les prototypes
  d'inventaire en lecture seule, réconciliation exacte, continuation locale
  bornée, verrou distant coopératif et checkpoints par journal. Ne pas les
  considérer comme des commandes supportées sur main. Le verrou ne protège
  pas contre un ancien exécuteur qui ne le respecte pas ; une migration
  explicite et une stratégie de récupération de lease seraient nécessaires.
  La CI 46119421887 a enregistré 37 succès et deux échecs de mocks ; correctif
  8bd28af proposé ensuite. Aucune recette mutatrice réelle ni purge locale
  globale n'est attestée ici.
- Toute éventuelle réactivation relève d'un nouvel arbitrage propriétaire,
  avec qualification de l'état réel et des preuves canoniques. Aucun
  déclenchement automatique au titre de cette capitalisation.
