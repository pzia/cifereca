# P1 #4530 — Paris/Syracuse : séparation facette / édition (06/11/2026)

## Qualification technique sans réseau

Les réponses `SearchService` Syracuse contiennent potentiellement des facettes globales `d.FacetCollectionList` : elles sont projetées comme **descripteurs de page de réponse** (`scope=response_page_only`), et non comme des champs d'âge associés à une manifestation ou à une édition.

Tests de non-régression sur deux scénarios synthétiques `CATALOGUE` et `EBOOKS` : facette `Audience` avec libellés « Dès 8 ans » et « 13-99 », plus des valeurs bibliographiques `Subj` / `Tags`. Le résultat conserve la facette avec son scénario, sa page et la capacité de filtrage **unverified**, mais ne publie aucun `recommended_age`, `attested_age_ranges`, `youth_age_ranges`, `attested_age_status` ni `public_age_range_label` au niveau du livre. Une requête ultérieure sans facette ne récupère aucune valeur résiduelle.

Ce contrôle prévient notamment le transfert de l'âge d'une facette multi-notices à chacune des éditions du résultat. Il ne prouve ni qu'un filtre d'âge côté Syracuse fonctionne ni qu'une édition possède une borne d'âge éditoriale.

## Dénominateurs et limites

| Source de test | Notices du fournisseur réellement examinées pour l'âge | Réponses synthétiques | Âges numériques par édition prouvés |
| --- | ---: | ---: | ---: |
| Paris CATALOGUE | 0 | 1 et scénario sans facette | 0 |
| Paris EBOOKS | 0 | 1 et scénario de reset | 0 |

Aucune connexion Paris ni requête réseau n'a été exécutée. Les noms de facettes et libellés d'âge sont **synthétiques** et n'attestent pas leur présence sur le serveur réel. Ne pas assimiler les classes bibliographiques, les facettes, les recommandations ou les niveaux scolaires à un âge numérique.

Suite #4530 : échantillon réel borné et autorisé par langue, identifiants d'édition exacts, provenance du champ, dénominateur d'absence et contradictions ; respecter le contrat de consolidation #4552. Aucune mutation de prêt, droit ou placement.
