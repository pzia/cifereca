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
borné à trois issues, 1 Mio de manifeste ; au-delà, refus sans mutation.

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
Pilote : dix commentaires modifiables au maximum ; gain contextuel positif
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

## Capitalisation différée

Sur demande, les LLM des rôles propriétaires inventorient
`staging/backlog/p0-*`, lisent les manifestes, vérifient les empreintes
et mutualisent les connaissances dans les documents/skills canoniques.
Les preuves uniques non capitalisables restent accessibles durablement
avant purge ; sans destination sûre, conserver le staging. Backlog
réceptionne la couverture sémantique complète, puis ferme completed sous
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
le parcours standard conserve 1 Mio. Les écritures sont fractionnées en tranches de dix,
avec un journal PROGRESS lié à l'empreinte et au curseur. Le staging intégral reste
présent jusqu'à DONE et capitalisation. Ne jamais purger un lot incomplet. Les journaux du
contrôle et les renvois des membres empêchent le rejeu ; ne pas réutiliser un contrôle.
La clôture du maître ne vaut pas réception des lots : inventorier leurs journaux.

### Poursuite d'un gros lot

Après `LOT_PENDING`, fermer sous pzia un contrôle sans label ni assignation nommé
`Poursuite consolidation #8195 — N` (N = contrôle de lot), avec JSON exact :

```json
{"version":1,"p0":123,"stage_sha":"<commit>","manifest_sha256":"<sha256>","main_sha":"<main>","cursor":10}
```

La reprise reconstruit le préfixe déterministe depuis le manifeste intégral, vérifie
le journal exact, tous les textes, les relations et le maître, puis exécute au plus dix
écritures nouvelles. Un ancien curseur, un écart ou une tranche partiellement appliquée
sans journal entraîne un refus sûr ; conserver le staging et qualifier manuellement.
DONE seulement après la dernière tranche. Aucun rerun, aucune purge avant DONE.
