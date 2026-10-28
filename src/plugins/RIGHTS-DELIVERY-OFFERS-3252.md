# Plugins P1 #3252 — offres de livraison observées, frontière Core #3171

Référence : 28 octobre 2026. Ce document qualifie les **faits attestés par les
adaptateurs fournisseurs**, jamais la décision d'acquérir ni une preuve de
fichier final. Le contrat public reste
`cifereca.rights.observation/1`. A = droit observé par le fournisseur ;
B = octets finaux validés, SHA et Placement avec OriginLink Core ;
C = Notice descriptive. A ne prouve pas B.

## Matrice des offres et actions réellement qualifiées

| Fournisseur | Branche et preuve positive A | Offre exacte utilisable | Action existante, effets et limite |
|---|---|---|---|
| Numilog | `meslivres`, achat actif, `lid+variant`, credential et compte exacts | **Scan ordinaire : `unknown`**. Préflight **ciblé et explicite** `rights_delivery_offer_snapshot(account, right_id)` (#3586 fusionnée) : `available` seulement si le HTML du compte relie une seule action JS `OwnedDelivery` à la carte achetée exacte. `delivery_offer_id` opaque : produit+exemplaire+type ACSM/EPUB et portée, sans URL signée. | `download_direct(item,target_dir,expected_offer_id=...,expected_identity_id=...)` (#3587 fusionnée) relit la carte avant GET signé et refuse les changements de propriétaire/offre. Import ADEPT/Core distinct si ACSM. La préqualification lit des pages **MesLivres**, pas le fichier. |
| BnF Prêt | `bnf_html`, section `active-loans`, `loan_id+resource_id` exacts | PR #3585 fusionnée : `available` **uniquement** lorsque le même HTML authentifié contient une action signée `reader-acs4` unique, correspondant au prêt actif et validée par le parseur existant. ID opaque dépendant de l'identité Core, credential, compte et des deux IDs fournisseur. Sinon `unknown`. | `download_digital(...,explicit_user_action=True,expected_offer_id=...)` reste un **token ACSM candidat** ; aucune activation automatique ou EPUB final n'est attesté. Le contrôle d'offer optionnel ne contourne pas l'action explicite. |
| Kobo | `personal_manifest`, produit acheté non ambigu, archives conservées | **Scan normal : `unknown`**. Préflight **explicitement sélectionné** `rights_delivery_offer_snapshot(account,right_id,explicit_user_action=True)` (#3592) : GET manifeste privé positif, puis POST `acquisition(mode=descriptor)` pour **une seule édition**. `available` uniquement pour un candidat HTTPS GET unique et admissible EPUB/PDF/ACSM, URL jamais exportée ; compte/credential/identité exacts et candidat contradictoire/KDRM => `unknown`. | `resolve_acquisition(...,expected_offer_id=...,expected_identity_id=...)` (#3592) requalifie l’offre fraîche et refuse une identité ou recette étrangère avant de rendre un descripteur privé. Consentement Core **avant tout préflight POST**, puis exécution Core distincte et vérification des octets finaux ; 403 délégué n’interdit pas les autres modes. |
| Paris | `dilicom`, `Id/HoldingId+RecordId` exacts ; état `unknown` | **`unknown`**. `AdditionalProperties.EbookUrl` est parfois un candidat PNB lié au prêt, mais la réponse ListLoans ne fournit pas encore de preuve fiable du droit `state=active`. | `download_digital` historique peut retourner ACSM ou contenu EPUB/PDF, parfois via `DownloadDigitalDocument` ; ne pas requalifier cette opération en offre automatique sur simple URL. |

**Source d'autorité :** seule la réponse privée authentifiée, sous le bon
broker d'identités Core, produit la preuve A. Aucun titre, ISBN, catalogue
public, fichier manuel, cache ou hash isolé ne devient un droit. Le plugin
n'affirme **jamais** la disparition d'un droit depuis un snapshot partiel.

## Protocole à raccorder exclusivement dans Core #3171

1. Lire **une seule** observation `ProviderRightsSnapshot` depuis le compte,
   credential, identité, branche et session exacts ; ne pas masquer les
   échecs/partiels par une liste vide réputée exhaustive.
2. Pour une offre déjà attestée ou un préflight ciblé : vérifier la
   `SourceKey` exacte (édition, droit, type et branche). **Ne lancer un
   préflight privé supplémentaire que sous une admission et un consentement
   Core explicitement configurés**, jamais pendant le scan général des
   bibliothèques. Le préflight Numilog cible un seul ID et ne GET pas le
   lien de téléchargement ; BnF réutilise le même HTML d'inventaire.
3. `delivery=available` indique uniquement un **chemin de livraison
   observable** : il ne prouve ni fichier final, ni ADEPT, ni permission Core.
   Vérifier `state=active`, `provider_response`, scope complet,
   `delivery_offer_id` non vide, autorisation et consentement persisté par
   `SourceKey` avant tout GET de contenu ; revalider après réseau.
4. Relire la même offre **sur la session actuelle** avant GET signé ; refuser
   les rebinds A→B et A→A, changement de credential/compte, action signée
   supprimée ou identité d'offre différente. Ne jamais sérialiser les URLs
   signées, tokens, cookies, jetons ACSM ou les headers privés.
5. Après livraison, achever ADEPT si nécessaire, inspecter les octets
   **finaux**, calculer le SHA final, associer un reçu d'exécution indépendant
   de A, puis laisser le **Core uniquement** publier
   `Artifact/Placement/OriginLink(provider)` atomiquement, sous son verrou.
   Le Core doit rehasher le Placement cible à la Sync suivante : présence
   exacte attestée => zéro nouveau GET ; rien n'est rapproché par ISBN/titre.

La callback `CompletedDelivery` des PR Core #3569/#3570 est **interne**
au Core, pas une API importable par les plugins. Ces PR ne doivent pas
être dupliquées. La nouvelle Sync ne possède pas encore de routage productif
de livraison ni de lecteur de consentement persisté : **aucune activation
automatique par le simple merge des PR Plugins**.

## Recette et limites

Tests offline : 0/1/N droits et comptes, action absente ou contradictoire,
signature qui tourne sans changement de l'édition, identité A→B pendant
GET/projection, broker A→A, credential étranger, doublons, 0 GET de contenu
sans offre/consentement, 1 GET autorisé, deuxième Sync 0 GET si origine et
Placement exacts. Les tests Plugins couvrent surtout la **qualification A et
l'arrêt avant GET** ; les tests Core couvrent consentement, transaction et
idempotence. La **recette terrain** reste nécessaire pour valider des vrais
jetons, signatures, EPUB/PDF finaux et ADEPT.

Ne fermer ni #3252 ni #3171 sur ces seules qualifications. Pas de file
parallèle de réconciliation ni de décision d'autorisation déportée dans B.
