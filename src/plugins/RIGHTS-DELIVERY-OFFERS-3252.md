# Plugins P1 #3252 — offres de livraison observées, frontière Core #3171

Référence : 28 octobre 2026. Ce document qualifie les **faits attestés par les
adaptateurs fournisseurs**, jamais la décision d'acquérir ni une preuve de
fichier final. Le contrat public reste
`cifereca.rights.observation/1`. A = ressource personnelle observée et
capacité de livraison qualifiée, avec état de droit facultatif ;
B = octets finaux validés, SHA et Placement avec OriginLink Core ;
C = Notice descriptive. A ne prouve pas B.

## Matrice des offres et actions réellement qualifiées

| Fournisseur | Branche et preuve positive A | Offre exacte utilisable | Action existante, effets et limite |
|---|---|---|---|
| Numilog | `meslivres`, achat positif `lid+variant` exact, identité/credential/compte attestés | Scan ordinaire `unknown`, préflight explicite sélectionné `rights_selected_delivery_offer_snapshot` (#3605), carte `OwnedDelivery` actuelle et identifiant d'offre opaque | `rights_selected_delivery_candidate` (#3606) relit l'offre et ne livre que l'EPUB direct ou l'**ACSM brut** (#3616) dans le staging demandé, sans import ADEPT legacy ; le download manuel reste inchangé. Broker exact et identité `default` historique contrôlée (#3612). |
| BnF Prêt | `bnf_html`, section `active-loans`, identifiants prêt + ressource exacts | Action signée `reader-acs4` unique, active et liée au prêt ; préflight optionnel #3605, `unknown` sans preuve | `rights_selected_delivery_candidate` (#3606) et `download_digital` exact produisent un **ACSM candidat** sous staging Core. Aucune activation ADEPT ni EPUB final attesté côté plugin. |
| Kobo | `personal_manifest`, produit exact du compte (état de droit `unknown` si `owned` absent), archives conservées | Scan ordinaire `unknown` ; préflight ciblé #3610 : manifeste positif + POST `descriptor`, offre HTTPS opaque unique (EPUB/PDF/ACSM), DRM/format cohérents (#3602/#3621), KDRM et contradictions exclus | `rights_selected_delivery_candidate` (#3618) relit la ressource exacte et l'offre, puis livre le candidat sous staging Core via client public à peer épinglé, sans proxy, cookie Vault ou redirect. Propriétaire revalidé et URL signée jamais retournée ; **Core seul** inspecte/re-hashe/ADEPT et publie. |
| Paris | `dilicom`, identifiants Id/HoldingId + RecordId et emprunteur exacts, **état toujours `unknown`** | Le `EbookUrl` privé courant sur la carte DILICOM exacte permet `delivery=available` et `paris.pnb:<hash>` lié au compte/édition/prêt, sans attester le statut de droit ni le succès HTTP | `rights_selected_delivery_offer_snapshot` puis `rights_selected_delivery_candidate` relisent le prêt et son action PNB avant GET ; route sélectionnée `download_digital(...,expected_right_id,expected_identity_id)` sans POST d'emprunt/quota de secours. ACSM/EPUB/PDF bruts candidat sous staging Core seulement, SHA/ADEPT/OriginLink restent à A. |

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
   Vérifier `provider_response`, scope complet, `delivery_offer_id` non vide,
   capacité positive d'acquisition, consentement persistant pour la source,
   et absence de refus fournisseur explicite ; **ne pas exiger `state=active`**.
   Un droit déclaré `active` avec offre indisponible ne déclenche rien.
   Revalider ces conditions après réseau.
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

La callback `CompletedDelivery` reste **interne** au Core :
aucun plugin ne l'importe ni ne publie de transaction SQLite. Le lecteur
de consentement persistant (#3601), le préflight Core (#3608) et le
routeur de candidat sous staging strict (#3613) sont fusionnés.
Le Core émet un témoin de retour `core-delivery:` **après** octets et
ADEPT éventuel, distinct de la preuve A fournisseur ; ce témoin n'est
pas présenté comme un reçu émis par le fournisseur. La projection
descriptive post-publication est livrée par #3617.
Aucune fusion de ces PR ne démontre à elle seule la recette live
fournisseur, la reprise crash matérielle ni une deuxième Sync à zéro GET
réel. Les tickets #3252, #3171 et #3228 restent ouverts.

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
