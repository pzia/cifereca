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

## Critères encore bloquants

1. **Terrain La Rochelle :** capture d'une nouvelle livraison autorisée sur
   un prêt réel actif avec offre fraîche, transit par `.canonical-delivery`,
   reçu, SHA, origine et Placement exacts, puis une deuxième Sync sans GET.
   Les captures de simple réutilisation `present_attested` ne valent pas
   premier téléchargement.
2. **Qualification des routes :** les formats et étapes Paris, Numilog,
   Kobo et BiblioAccess restent soumis à #7231 ; seules les phases dont le
   fournisseur/runtime atteste la rejouabilité peuvent alimenter #7161.
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
