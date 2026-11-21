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
   Le système existant permet uniquement de comparer avec des notices C
   **publiques déjà connues**. Le résultat paginé/partiel ou périmé reste
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
la **comparaison locale hors réseau et sans mutation**. Le package
indépendant #7092 est réceptionné ; cela ne clôt **pas** #4030.

Restent à développer et qualifier explicitement : consentement privé par
surface, recherche de catalogues distants en budgets/pages, projection
multi-sources avec provenance de valeur et fraîcheur, déclaration volontaire
réversible dans Core et son UX, cas d'éditions équivoques. Aucun lien provider
attesté ou droit n'est créé par l'implémentation documentaire actuelle.

**Garde de production** : ni activation destructrice #6937/#3176, ni
levée de marqueur ADEPT #7244, ni nouveau chemin de publication.
