# P0 #4030 — surfaces de catalogue et niveaux de preuve

**Statut : contrat métier ; pas une capacité de recherche distante supplémentaire.**
Ce document complète [ARCHITECTURE.md](ARCHITECTURE.md),
[PLUGIN-BOUNDARIES.md](PLUGIN-BOUNDARIES.md) et
[CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md](CORE-P1-2385-CATALOGUE-INVENTORY-CONTRACT.md).
Il n'autorise ni nouveau GET, ni prêt, ni attribution de provenance.

## Trois familles non exclusives

La classification s'applique à chaque **surface / opération / portée** attestée,
et **non** exclusivement au nom d'un fournisseur.

| Famille de surface | Faits admissibles | Preuve insuffisante |
| --- | --- | --- |
| Catalogue public | Notice C publique avec source, édition, révision, date, fraîcheur, couverture/pagination | Droit, origine réelle des octets, domaine public présumé |
| Inventaire d'achats personnel | Inventaire d'un compte authentifié, titre acheté et offre éventuelle sous compte/credential exact | Fichier local téléchargé, licence transmissible, acte d'achat par une suggestion |
| Inventaire de prêts | Occurrence et état de prêt courant dans une branche/compte exacts, livraison éventuelle | Prêt créé par une recherche, titre disparu si lecture partielle, autorisation de retour |

Exemples **indicatifs, à qualifier par contrat effectif** : Kobo et Numilog
peuvent exposer catalogue public **et** achats ; Paris/BnF des notices et des
prêts ; Gallica/Gutenberg/Ebooks libres et gratuits, catalogue public.
Une extension technique/transformation ne constitue aucune famille métier.
Un catalogue public n'est pas nécessairement un catalogue de ressources
librement téléchargeables.

Les capacités demeurent distinctes :
`catalog.search`, `personal_inventory`, `loan_inventory`, `download`.
`CatalogueSourceCapability` v1 est un indicateur de disponibilité
(cache-only, lecture seule), **pas** une licence de requêtes de masse ni une
qualification de `catalog.refresh` : ce dernier demeure non pris en charge.

## Contrat de proposition d'origine locale

1. **Éligibilité locale** : sélection explicite, profil actuel, Placement
   `present`, Artifact/SHA/fichier revalidés. Toute origine provider/ADEPT
   ou reçu historique même partiellement contradictoire est un veto ;
   aucun fichier privé n'est transmis à un catalogue.
2. **Preuve documentaire** : OPF EPUB ou titre/auteur C local vérifiés.
   Le système compare les notices C **publiques déjà connues**, et propose
   sur sélection explicite une recherche publique et une comparaison des
   inventaires privés conservés. Le résultat paginé/partiel ou périmé reste
   explicitement partiel ; zéro correspondance ne prouve aucune origine.
3. **Recherches futures** : une surface distante n'est interrogée que si le
   plugin déclare et implémente le champ et le budget, si l'utilisateur
   demande la recherche, si le compte/scope est valide et si le consentement
   **spécifique à l'outil** existe. Afficher/ouvrir l'outil n'est jamais un
   consentement. Préserver le caractère partiel de 401/403/429, timeout,
   annulation, identités basculées et pagination.
4. **Classement des candidats** : concordance de métadonnée exacte de source
   **dans le fichier** d'abord ; édition/ISBN corroborés ensuite ; seule
   similarité de titre/auteur en dernier. À preuve égale, préférer publics,
   puis achats, puis prêts. Ne jamais écraser une preuve exacte privée par
   un catalogue public prioritaire. Différentes éditions ambiguës restent
   des candidats distincts.
5. **Trois autorités** : *Notice C suggérée* (non liée) ≠
   *origine déclarée* (`declared/unverified`, opt-in révocable) ≠
   *OriginLink(provider/adept) attesté* (preuve A/B transactionnelle de
   réception et des octets exacts). Un clic sur une suggestion n'est aucune
   des deux dernières mutations ; la déclaration éventuelle doit posséder
   un broker Core, un audit et un rollback distincts.
6. **Décisions réseau** : aucune recherche documentaire ne permet `borrow`,
   achat, ACSM/ADEPT, téléchargement ou réactivation d'un droit ; les
   parcours #5225/#7161 ont leur consentement et leurs gardes indépendants.

## Répartition des responsabilités

- **Plugin fournisseur** : inventaires/surfaces, provenance des réponses,
  pagination, fraîcheur, erreurs et limites de transport ; aucune écriture
  SQLite privée ni association à un Artifact local.
- **Core** : admission et source exacte, provenance existante, preuve des
  octets, capacités exposées, contrôles de consentement et futurs writers
  transactionnels de déclaration ; aucune inférence d'origine par SHA/ISBN.
- **CoreUI / Outils** : lancement explicite, sélection du Placement, rapports
  comparatifs et états partiels, annulation/reprise, confirmation éventuelle ;
  aucune recherche au simple affichage.

## Couverture actuelle et reste à faire

Le contrôleur Core `local_origin_suggestion_preview.py`, les pages de
`local_origin_suggestion_batch.py`, l'extension Maintenance
`maintenance-local-origin` et les notices C publiques vérifiées satisfont
la **comparaison locale hors réseau et sans mutation**. Le détail peut aussi
interroger explicitement une surface publique et comparer, après consentement
spécifique, une fenêtre d'achats/prêts en cache du compte connecté, avec pages
et révocation. Les descriptions divergentes d'un achat restent des candidats
ambigus ; aucune valeur canonique choisie. Le package
indépendant #7092 est réceptionné ; cela ne clôt **pas** #4030.

### Preuves intégrées au 22/11/2026

- #7457 : relecture explicite de l'inventaire de prêts du compte exact, via
  contrat typé uniquement, sans transmettre le titre local. La comparaison
  reste descriptive ; branche canonique, couverture exhaustive et fraîcheur
  réseau ne sont pas réputées prouvées par cette lecture.
- #7471 et #7508 : choix sur les pages privées, aperçu typé de prêt ou d'achat
  existant et vérification de l'occurrence exacte. Un droit owned actif
  unique est nécessaire pour l'achat ; un droit temporaire, une édition
  seule ou une ambiguïté ne permettent pas la déclaration d'un achat.
  L'aperçu ne modifie ni droit, ni fichier, ni Placement, ni OriginLink.
- Le bridge Core réutilise le writer de déclaration #2797, sans créer un
  writer ou un port mutateur plugin supplémentaire. La déclaration reste
  distincte de la provenance attestée et révocable par le parcours existant.

La confirmation native de l'aperçu privé (#7523) est intégrée sur `main`
(squash `027d329e`, quatre gates vertes). Le parcours opérateur est décrit
dans le [guide du plugin](../src/plugins/maintenance-local-origin/README.md). Les limites
sur les recherches privées distantes, la couverture multi-sources, la
fraîcheur et les ambiguïtés restent opposables ; aucune preuve terrain ni
clôture #4030 n'est déduite de cette mise à jour documentaire. Aucun lien
provider attesté ni droit n'est créé par les rapprochements.

### Matrice de réception croisée — preuves réutilisables

Cette matrice relie les critères existants aux témoins de `main`.
Elle ne transforme **ni un résultat C en provenance**, ni un test
synthétique en preuve terrain ou en autorisation de GET.

| Critère #4030 | Preuves synthétiques | Limite opposable |
| --- | --- | --- |
| Placement exact, fichier/SHA, profil, exclusion des origines attestées même sous `other/` | `test_core_p0_4030_local_origin_suggestion_preview.py`, `test_core_p0_4030_scoped_documentary_suggestions.py`, `test_core_p0_4030_candidate_exclusions.py` | La présence physique seule n'atteste pas la source |
| Notice C, attributs OPF/ISBN, éditions concurrentes et rang descriptif | `test_core_p0_4030_epub_evidence.py`, `test_core_p0_4030_documentary_suggestions.py`, `test_core_p0_4030_public_catalogue.py` | Pages partielles, aucun rang universel, droit/format non déduits |
| Sélection explicite de catalogues publics, 401/403/429/timeout, activation révocable | `test_core_p0_7092_origin_detail_public.py`, `test_core_p0_7092_origin_detail_controller.py` | Seul le titre OPF est envoyé après choix ; résultat distant non exhaustif |
| Achats/prêts privés exacts, cache et continuation, choix d'un compte | `test_core_p0_4030_private_catalogue.py`, `test_core_p0_4030_real_purchase_c_comparison.py`, `test_core_p0_4030_private_cache_evidence_order.py` | Cache non réputé frais, dénominateur de couverture non attesté |
| Relecture typée du seul inventaire de prêts sélectionné | `test_core_p0_4030_remote_loans.py` | Ne prouve aucune branche canonique, activation ni droit |
| Priorité transverse sur les fenêtres explicitement consultées, classement par preuve puis public/achats/prêts à preuve égale | `test_core_p0_4030_cross_surface_ranking.py` (#7711, fusion `633029d`, PR CI verte) | Un ISBN revendiqué par plusieurs éditions reste ambigu ; classement documentaire seulement, une provenance attestée n'est jamais rétrogradée |
| Occurrences publiques de même ISBN et référence, formats distincts, provenance de chaque coordonnée et âge de l'observation C locale | `test_core_p0_7092_origin_detail_public.py`, `test_coreui_p0_4030_public_catalogue_action.py` (#7713, fusion `3a75682e`, PR CI 45066973889 verte) | Chaque format reste un candidat distinct ; la date C est l'observation conservée, ni fraîcheur réseau ni acquisition prouvée |
| Déclaration humaine distincte des attestations, consentement et révocation transactionnelle | `test_core_p0_4030_loan_declaration_bridge.py`, `test_core_p0_4030_purchase_declaration.py` | Writer existant #2797, droit exact préalable obligatoire, jamais `OriginLink(provider)` inventé |
| Batch supervisé, annulation/ABA, scopes et budgets | `test_core_p0_4030_suggestion_batch.py`, `test_core_p0_4030_batch_native_reception.py` | Comparaison C locale seulement ; pas de balayage réseau silencieux |
| Cohabitation des trois surfaces, 403 public sans veto privé, révocation de profil, invariance FS/SQLite | `test_core_p0_4030_three_surface_reception.py` (#7707, fusion `bc29ce1`, PR CI verte) | N'exécute ni téléchargement fournisseur ni prêt réel |

### Réception technique vérifiée — 24/11/2026

Les preuves suivantes sont intégrées à `main` (ancêtres vérifiés) et
leurs workflows PR CI sont verts sur les HEAD respectifs :

| Lot | Fusion | PR CI | Portée |
| --- | --- | --- | --- |
| #7713 | `3a75682e2ec5a65fd5637c6c32abfd91eaea15c3` | 45066973889 | Occurrences, éditions/formats distincts, projection locale |
| #7749 | `6698c3cdc6ed3a8c6b52e52bdc4b8785eb97a770` | 45120325468 | Adaptateurs Gallica/Gutenberg, revue locale supervisée |
| #7802 | `2ee32e41d0ca6ff96aba8caf857c038b398e7cf3` | 45127570530 | Adaptateur EbooksGratuits OPDS, requête de titre explicite sans livraison |
| #8019 | `79b15df5414b9925fa694416693577cbabf4171e` | 45480112516 | Quatre parcours synthétiques : 401/403/429/timeout public, comparaison privée paginée, déclaration par writer canonique, refus du curseur périmé et révocation |

Le témoin #8019 réunit sur un même Placement/EPUB et une même base SQLite
les incidents publics, le consentement privé et le cycle réversible de
déclaration. Les droits, SourceReference, OriginLink, Artifacts, Placements,
catalogue, ledger et octets sont inchangés. La déclaration reste un énoncé
utilisateur, jamais une acquisition attestée. Les fenêtres et le cache
restent partiels et non qualifiés frais. #8025 ajoute une preuve ciblée
de TTL négatif contextuel, décrite ci-dessous ; aucune recherche distante
privée exhaustive ni preuve terrain n'en est déduite.

### Cache négatif contextuel reçu — 24/11/2026

#8025 est intégré à main (fusion `e3f67b4b2f4ab22cd35450e361ca23be02ce04e7`,
HEAD `75d024b21462f548e2270d572d77594fcce34377`, PR CI 45483447676,
installation 45483447772 et AST 45483447654 success).
Une réponse anonyme vide, complète, non partielle et explicitement
marquée `terminal_empty` peut être réutilisée au maximum 30 secondes
(monotonic), pour une seule entrée dans le broker contextuel actif.
La nouvelle comparaison exige toujours un consentement explicite et
la revalidation exacte du fichier, des coordonnées et de l'image SQLite.
L'annulation et la révocation effacent le cache ; changement de session,
profil, epoch, requête ou état SQL interdit sa réutilisation. La réutilisation
est affichée comme telle, sans nouvelle requête ni preuve de fraîcheur.
Neuf cas synthétiques : expiration, absence de marqueur, résultat partiel,
couverture inconnue, échec, annulation et dérives de scope.
Ce mécanisme ne couvre pas l'ancienne action GUI indépendante, ne stocke
aucun payload fournisseur et ne qualifie ni absence de droit ni absence
exhaustive d'un document chez le fournisseur.

### Achats distants et limites de fenêtre reçus — 25/11/2026

Les lots suivants sont fusionnés sur main, avec vérification d'ascendance et
gates vertes sur leurs HEAD respectifs :

| Lot | Fusion | PR CI | Réception |
| --- | --- | --- | --- |
| #8045 | `c0ada4e757603ff97470ce2bcf0107e34a865dc7` | 45488328432 | Primitive explicite via PurchaseEnrichmentObservation, cinq cas |
| #8055 | `1a389426aa192f05e5ee7b0b57d9a9b6d52d9b11` | 45491640172 | Port contextuel, action dédiée, choix bornés et révocation, dix cas |
| #8057 | `517255c7d73d1ff5fd0b3ab3b25e485cae4f7b11` | 45492268843 | Sept cas de limites, omissions, homonymes et intégrité des tables SQLite |
| #8059 | `f49e2a7905bcac8feb234f6f4e5fb0a96bb8dd7c` | 45492811632 | Trois cas avec le vrai parseur Numilog et transport HTTP simulé |

#8055 : E2E 45491640224, AST 45491640200 et installation 45491640117
également success. Les comparaisons de prêts #8032 (fusion 4ed052e,
PR CI 45485126151) qualifient les branches numériques déclarées, jamais
l'exhaustivité globale. #8037 (fusion e0683a2, PR CI 45485781988) conserve
les limites de consultation C et les sources non indexées dans le batch.
#8039 (fusion 8670650, PR CI 45488404851) et #8040 (fusion 31cb446) refusent
les promotions URL/ISBN ambiguës. #8047 (fusion b68b2cf, PR CI 45489381151)
publie atomiquement la continuation privée et la fenêtre de déclaration.

Le choix « Relire les achats » est distinct de la comparaison locale en
cache, de la synchronisation et du consentement à l'enrichissement public.
Le Core consomme seulement le port typé existant du compte connecté,
sans fallback vers un autre inventaire. Le titre privé lu est comparé
localement ; le titre et les octets locaux ne sont pas transmis. Un
locator public éventuellement transporté par le DTO n'est ni suivi,
ni affiché. Les candidats ont titre concordant, auteur inconnu ; deux
éditions homonymes restent distinctes. Aucun curseur distant n'est suivi
automatiquement par le comparateur ; un producteur peut effectuer sa
propre lecture paginée bornée selon son contrat. Aucun résultat ne
devient une déclaration ou une preuve A/B par ce seul chemin.

La réception Numilog concerne une session authentifiée synthétique,
le vrai parseur et un HTTP MockTransport. Elle ne constitue pas une
lecture réelle du compte ni une qualification de tous les fournisseurs
d'achats. Les autres fournisseurs sans ce port restent sur leur surface
connue admissible. Aucun marqueur de couverture globale ou de fraîcheur
réseau n'est affirmé.

**Critères de clôture encore à arbitrer en réception :** la somme des témoins
ci-dessus doit être confrontée au parcours complet de l'utilisateur, dont les
priorités public/achats/prêts sont **des priorités de présentation, pas un score
juridique transversal**. Les résultats paginés et inaccessibles restent
partiels. La réception #8025 porte sur le TTL local de réutilisation,
non sur la fraîcheur réseau du fournisseur. Aucune revendication de
rafraîchissement privé exhaustif ni de preuve terrain sans témoin
explicite ; aucun nouveau moteur de recherche n'est autorisé par cette matrice.

**Garde de production** : ni activation destructrice #6937/#3176, ni
levée de marqueur ADEPT #7244, ni nouveau chemin de publication.

### Réception des budgets et de la reprise supervisée — 25/11/2026

| Lot intégré | Fusion | PR CI |
| --- | --- | --- |
| #8063 | `3634d8e60e70f4ff7078db440d01f569f5e8a8d3` | Gates PR, AST et installation vertes |
| #8064 | `e13cb85e588863aff6a1278db563fa9244e463fc` | 45555914756 |
| #8066 | `859eebc295b72f350c7a1644161faf7f2c0c4d29` | 45558307649 |

Le broker contextuel limite les admissions explicites de comparaison distante
à 8 par fournisseur et 32 au total sur une fenêtre glissante de 60 secondes,
par activation. Une nouvelle preview ou une annulation ne réinitialise pas
ce budget. Ce compteur ne mesure pas les requêtes HTTP internes aux producteurs,
ni les autres activations ou processus : aucun quota réseau global n'est attesté.

Le batch local reste sans réseau, avec plafond de 128 Mio de travail estimé,
3 secondes coopératives entre fichiers et 200 pages au maximum. Le curseur
est lié à la revue et à l'image SQLite, non à un checkpoint durable. La reprise
sur la page suivante est supervisée ; annuler révoque la continuation en vol,
et un changement de scope impose une nouvelle revue. Aucun fichier trop gros
n'est déclaré comparé. Le plafond temporel n'est pas un timeout I/O strict.

Ces preuves ne suffisent pas à déclarer une reprise persistante après arrêt
du processus. Les correctifs #8072 et #8073 restent à réceptionner séparément.
Aucun élargissement de permission, aucune attribution de droit ou provenance,
aucune qualification terrain ne découle de cette réception.
