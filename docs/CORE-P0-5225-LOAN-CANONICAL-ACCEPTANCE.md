# P0 #5225 — prêt automatique : deux chemins et critères de réception

État technique au 21/11/2026. Cette matrice ne constitue ni une autorisation
d'acquisition, ni la preuve d'un téléchargement authentifié La Rochelle.
Distinguer les **fonctions encore présentes** des routes effectivement
exécutées sur un compte activé et de leurs témoignages d'exécution.

## Comparaison des frontières

| Étape | Façade historique / compatibilité | Convergence canonique A→B |
| --- | --- | --- |
| Entrée | `LibraryService._acquire_active_loan_automatically_guarded` ; admission historique d'un `Loan` | `sync_coordination._run_observed_canonical_convergence` depuis ouverture/Sync source ; `qualify_selected_offers` puis `execute_admitted_convergence` |
| Ressource | Entrée `Loan`, garde d'identité/compte, historique `acquisition_ledger` | `ProviderRightsSnapshot`, `SourceKey` exact (identité, fournisseur, credential, compte, branche, édition, droit, type), offre positive et consentement actuel |
| Livraison | `loan_provider_delivery.acquire_provider_loan` et traitement `loan_adept_fulfillment` / publication spécialisée ; les contrats historiques ne prouvent pas à eux seuls un B canonique | `canonical_selected_delivery.complete_selected_delivery` ; éventuel ACSM privé, contrôle de profil avant le runtime quand le format est attesté, contenu final inspecté |
| Fichier temporaire | Workdir ACSM / chemin spécialisé, non assimilable à `.canonical-delivery` | `<profil>/.canonical-delivery/run-…/` ; octets finaux, format, SHA et reçu de livraison indépendant |
| Reçu avant B | Ledger historique d'opération, non preuve de propriété canonique | `canonical_recovery.record_completed_delivery_for_recovery` : journal durable `canonical_receipt_staged`, exact `source+offer+sha+receipts`, puis libération de l'essai ADEPT **de cette opération** |
| Publication | Index et journal `acquisition_put` historiques ; ne pas déduire un `OriginLink(provider)` d'un fichier présent | `canonical_promotion`, puis un `canonical_publication.publish_completed_acquisition` : Artifact + Placement profil + SourceReference + OriginLink(provider/adept) dans une transaction SQLite |
| Durabilité | Ne pas présumer l'équivalence des garanties sur la seule base du statut `complete` | Reçu, B et transition de ledger sous `PRAGMA synchronous=FULL`, `BEGIN IMMEDIATE`, puis restauration du mode normal (#7306) |
| Après B | Le nom d'un fichier ou un SHA historique ne suffit pas pour dispenser d'un GET | B vérifié (source exact + bytes FD) → zéro GET ; nettoyage staging après B seulement, non autoritatif ; échec d'index/dédup ne défait pas B |
| Redémarrage | Ne pas utiliser le ledger historique comme un `CompletedDelivery` | Récupération du reçu exact si fichier final encore vérifiable, consentement et compte réadmis ; aucune nouvelle livraison ; sinon refus ou nouvelle livraison seulement si offre et anti-rejeu l'autorisent |

Les deux façades n'ont pas à être supprimées pour la réception : l'exigence
est que **toute nouvelle acquisition de prêt empruntant la Sync canonique**
passe par un seul contrat d'admission, de publication et de reprise.
La présence de l'ancienne méthode dans le dépôt n'atteste pas son invocation
pendant les sessions de terrain.

## Matrice des témoins (sans réseau réel)

| Cas de recette | Éléments vérifiés | Limite |
| --- | --- | --- |
| Nouveau prêt BiblioAccess, format ACSM connu | `tests/test_core_p0_3197_biblioaccess_convergence.py` : transport MockTransport, véritable sélection d'offre/Sync et staging, ADEPT simulé, B exact, deuxième Sync `present_attested` et zéro GET | Pas de nouvelle délivrance authentifiée La Rochelle |
| Prêt avec consentement OFF, inconnu, désactivé ou périmé | Même suite : aucun GET non admis, pas de B inventé | Pas une preuve exhaustive de toutes les branches fournisseurs |
| Échec avant fin d'ADEPT, issue ambiguë et hold | Même suite : marker durable exact, ni second GET, ni emprunt, ni réutilisation d'un `acquisition_ledger` legacy comme B | Un marker v1 historique ne prouve pas la phase d'appel |
| Panne après reçu, avant promotion ou avant publication | #7305 : promotion/publication interrompue × Sync ouverture/source, reçu et octets conservés, reprise sans nouveau GET/ADEPT | Exceptions synthétiques |
| Panne **au milieu** du commit SQLite B | #7318 : trigger SQLite `BEFORE INSERT origin_links`, rollback des insertions Artifact/SourceReference/Placement ; reprise sur le même reçu, troisième Sync idempotente | Injection déterministe, pas simulation de coupure secteur |
| Durabilité et transaction appelante | #7306 ; `tests/test_core_p0_3171_atomic_publication_v14.py` et `tests/test_core_p0_5225_canonical_recovery_snapshot.py` : FULL, ordre COMMIT→restauration, rollback et isolation | Test SQLite, pas preuve matérielle de coupure d'alimentation |
| Scope, rebind, offre changée, reçu étranger | `tests/test_core_p0_3171_crash_recovery_v14.py`, `tests/test_core_p0_3171_convergence_execution.py`, `tests/test_core_p0_5225_canonical_recovery_snapshot.py` | Les captures sont fictives |
| Pause de contenu sans perte de B et multi-fournisseur | #7324 : vrai Core/SQLite, deux fournisseurs, prêt/achat, troisième tentative, budget exact, reçu prioritaire malgré pause, puis zéro GET | Ne qualifie pas le rejeu d'une mutation ADEPT ambiguë (#7161/#7231) |
| Ancienne archive/ledger | Les recettes BiblioAccess conservent le journal historique et refusent son adoption sans reçu B canonique | La compatibilité de tous les fichiers utilisateurs ne se déduit pas d'une fixture |


## Protocole terrain minimal — première livraison La Rochelle

**Précondition** : compte légitime déjà connecté, prêt numérique réellement
actif et déjà emprunté, consentement Sync valide ; ne pas déclencher de
`borrow` ni d'achat pour construire la recette. Conserver une copie locale
sûre de la base et de la bibliothèque avant le test. Utiliser une version
installée correspondant au HEAD/CI reçu et indiquer ce SHA dans le relevé.
La première observation doit concerner un prêt sans B canonique locale
préexistante ; une simple resynchronisation de copies présentes n'est pas
une première livraison.

| Contrôle | Première Sync | Deuxième Sync, **même compte/prêt** |
| --- | --- | --- |
| A et offre | Référence emprunteur/branche/édition/occurrence exactes, offre fraîche, consentement ON | Nouveau contrôle de portée, offre sans emprunt induit |
| Transfert et traitement | Au plus un GET de contenu autorisé, ADEPT seulement si l'ACSM est attesté ; aucun `borrow` | **Zéro** nouveau GET/ADEPT du contenu déjà vérifié |
| Journal | Reçu durable `canonical_receipt_staged` puis terminal `complete` après B | Aucun nouveau reçu d'acquisition pour cette occurrence |
| Publication B | Artifact SHA final, SourceReference exacte, OriginLink(provider et ADEPT si reçu), Placement `present` **dans le profil** | Même identité/SHA/provenance, B toujours satisfaisant et physiquement présent |
| Résultat | `downloaded` / `canonical_delivery_committed` ; absence de fichier partiel publié | `present_attested` / présence canonique constatée, sans nouvelle acquisition |

**Preuves à garder localement.** Relever l'heure, le commit installé, les
codes de résultat des deux Sync et les compteurs de GET, fulfillment, POST
`borrow`, rollback et reprise ; vérifier l'Artifact/OriginLink/Placement
avec les diagnostics locaux exacts et la preuve de SHA. Les statuts doivent
être associés à **la même occurrence**, pas seulement au même titre ou SHA.
Distinguer explicitement capture applicative, trace HTTP instrumentée et
commandes exécutées par le sous-processus ADEPT : une trace HTTP Core ne
compte pas les échanges internes du runtime. Noter chaque élément qui n'a
pas été instrumenté comme « non vérifié », jamais comme « zéro ».

**Confidentialité.** Ne joindre à GitHub que les états agrégés et un reçu
de test *expurgé* : ni identifiant de compte, ni numéro de carte, ni chemin
privé, ni licence/ACSM, ni URL signée, ni token/cookie, ni secret ADEPT.
Garder la corrélation exacte en local et confirmer séparément qu'elle a
été contrôlée. Une capture documentaire n'autorise aucun reset de marqueur
v1 historique ; ce parcours reste sous #7244.

**Refus de réception** : premier fichier déjà présent, URL déduite d'un
titre/ISBN, GET répété sans qualification, second POST de prêt, journal
`complete` sans preuve B, déplacement automatique du fichier, copie
corrompue/symlink acceptée, compte ambigu, coupure non instrumentée ou
retour d'une phase ADEPT incertaine. En cas d'incertitude, conserver octets,
reçus et journal ; ne supprimer ni staging ni marqueur pour « faire passer »
la recette.

Le contrôle transactionnel de la transition vers `complete` a été renforcé
par #7333 : SourceReference provider attestée, OriginLink reçu exact et
Placement du profil doivent exister au même instant SQLite. #7349 ajoute
la revalidation de B lors de la relecture idempotente du journal déjà
complet (**à considérer livrée uniquement après CI et fusion**).
Les scénarios synthétiques #7305/#7318 valident rollback et reprise, sans
prétendre reproduire une panne de courant matérielle.

## Critères encore bloquants

1. **Terrain La Rochelle :** capture d'une nouvelle livraison autorisée sur
   un prêt réel actif avec offre fraîche, transit par `.canonical-delivery`,
   reçu, SHA, origine et Placement exacts, puis une deuxième Sync sans GET.
   Les captures de simple réutilisation `present_attested` ne valent pas
   premier téléchargement.
2. **Qualification des routes :** #7231 est techniquement réceptionnée
   (Paris, Numilog, Kobo, tests synthétiques et matrice) ; chaque fournisseur
   reste soumis aux preuves de phase, compte, offre et consentement lors du
   GET réel. L'absence de preuve de rejeu d'une mutation ADEPT ambiguë
   interdit toujours la reprise automatique ; #7161 n'efface pas #7244.
3. **Cas legacy résiduels :** observer les chemins effectivement invoqués,
   pas uniquement leurs signatures ; un fichier déjà valide ne doit pas
   être déplacé ou re-téléchargé par principe. Conserver les reçus et
   fichiers antérieurs sur erreur.
4. **Sortie de lot :** CI, installation et rollback sur le HEAD retenu,
   démonstration hors réseau de réutilisation/délivrance et constat terrain
   séparé. Ni dégel destructif, ni release automatiquement induite.

Les coordonnées et reçus ci-dessus sont des contrats internes : aucun token
signé, activation, information de compte, URL de livraison ou chemin privé
ne doit être publié dans les logs de recette.
