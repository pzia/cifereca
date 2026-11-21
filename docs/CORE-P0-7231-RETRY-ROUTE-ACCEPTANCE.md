# P0 #7231 — matrice de reprise par route de livraison

État du contrat au 21/11/2026, en coordination avec #5225, #7161 et #7244.
**Observation ≠ permission** : ce document ne crée ni retry ni droit. Toute
exécution passe par l'offre fournisseur fraîche, le scope exact, le consentement
ON courant, le verrou Core, puis le seul pipeline publication canonique.

| Route attestée | Étape et preuve minimale | Répétition admise | Échec fermé, non assimilable à un retry | Recette synthétique |
| --- | --- | --- | --- | --- |
| BiblioAccess/BnF ACSM | Contrôle de profil avant ADEPT, marker de tentative avant dispatch | Refus **live** certifié non soumis : libération de **cette** tentative ; nouveau Sync seulement si même prêt actif, activation/profil et offre courants | Résultat ADEPT ambigu ou ancien marqueur v1 dont la phase n'est pas attestée : verrou conservé | `tests/test_core_p0_3197_biblioaccess_convergence.py`, `tests/test_core_p0_7161_*` |
| Paris/Dilicom | Format constaté à partir du contenu HTTP sélectionné ; contrôle sur chaque étape | GET de contenu déjà admis, interruption de corps HTTP 200 et scope inchangé : candidat de reprise **après** nouvelle admission | Erreur d'en-têtes, redirection, 403, profil ACSM indéterminé, révocation : aucune phase de transfert retentable inférée | `src/plugins/provider-paris/tests/test_p0_3867_public_redirects.py` |
| Numilog EPUB direct | Carte de possession et offre exacte de la même branche, GET signé puis staging | Échec transitoire qualifié du **transfert EPUB direct**, sans perte du compte/lease | ACSM, readBook, confirmation, POST ou route non démontrée rejouable : refus `*_replay_unqualified`, pas de classement comme retry direct | `src/plugins/provider-numilog/tests/test_p0_7231_retry_route_proof.py` |
| Kobo natif | Manifest exact et candidat HTTP choisi, admission Core et contrôle chunk/redirect | GET contenu interrompu sur transport attesté, si propriétaire, broker et lease inchangés ; nouveau cycle sous offre actuelle | Contenu partiel, URL/mode non admis ou mutation de compte : nettoyage staging et refus ; aucun droit issu du SHA | `src/plugins/provider-kobo/tests/test_p0_3252_kdrm_canonical_runtime.py`, `src/plugins/provider-kobo/tests/` |
| Kobo KDRM délégué | Offre opaque `kobo.runtime:` liée au manifest et au replay délégué, commande runtime Core gardée | **Seul** `RuntimeDispatchNotSubmitted` de la tentative live, après revalidation, distingue refus avant dispatch et permet relâchement de sa propre revendication | Exception après entrée, résultat ambigu ou changement de scope : verrou de runtime conservé ; pas de fallback HTTP ou nouveau GET en boucle | `src/plugins/provider-kobo/tests/test_p0_3252_kdrm_canonical_runtime.py`, `tests/test_core_p0_3171_selected_runtime_delivery.py` |

## Lecture commune de la file Synchronisation

- Le Core incrémente **une fois par cycle** uniquement un échec de **contenu**
  explicitement qualifié `RetryableContentDownload`. À 3, il met en pause
  cette identité + provider + credential + compte + branche + édition + occurrence.
- `RuntimeDispatchNotSubmitted`, `AdeptFulfillmentNotSubmitted` et un
  refus d'admission ne sont pas trois essais de transfert. Les marqueurs
  ADEPT/runtime et les attentions historiques gardent leur propre veto.
- Copie B exacte, fraîche et physiquement vérifiée **ou** reçu final de
  livraison admissible priment sur tout nouveau GET et sur la pause.
  Le compteur ou sa remise à zéro ne provoquent jamais emprunt, achat,
  activation, fulfillment ou nouvelle attribution de droit.
- Une erreur ou un inventaire partiel ne prouve ni absence de droits ni absence
  de fichier. Une décision sur un même titre/ISBN/sha/C n'autorise pas un GET.

## Limites de recette

- BnF : une reprise terrain du prêt déjà actif a été capturée ; pas de
  nouvelle mutation fournisseur exigée pour cette matrice.
- La Rochelle : la reprise de reçu ADEPT après échec de promotion ou de
  publication est simulée par MockTransport, et ne prouve pas un nouveau
  téléchargement authentifié sur un prêt réel.
- Paris ACSM non attesté avant réception : ne pas inventer de préflight réseau
  universel. Numilog readBook/confirmation et Kobo KDRM ambigu : pas
  d'autorisation de rejeu généralisée.
- Les exemples doivent rester hors réseau, hors livre utilisateur, sans
  suppression de staging/ledger ni émission d'identifiants ou secrets privés.
