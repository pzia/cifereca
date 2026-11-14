# #4681 — Corpus de régression de pertinence et de diversité

**Portée :** test offline `tests/test_p1_4681_relevance_metrics_corpus.py`.
Cette preuve ne résout pas à elle seule l'intégration future de Notice C
(#4552/#2385), ni l'évaluation humaine des recommandations.

## Ce qui est comparé

- **Entrées constantes :** 3 livres avec identifiants de bibliothèque exacts,
  4 notices candidates, 2 observations de classement de catalogue identiques
  pour éviter toute différence de rang RRF ou de requête fournisseur.
- **Baseline :** aucune préférence explicite, mode neutre.
- **Traitement :** deux livres « peu intéressant » et un livre « aimé »,
  tous saisis explicitement pour une identité et des clés exactes.
- **NDCG@2 :** mesure locale du rang selon les jugements *éditoriaux fictifs*
  du corpus (`rouge=0`, `bleu=3`), valeur 0 pour les deux premières
  notices neutres vs 1 dans l'ordre préféré. Le classement complet contient
  toujours les quatre notices : l'avis déplace les rangs, il ne censure pas
  les offres du catalogue.
- **Diversité@2 :** nombre d'auteurs distincts dans les deux premières
  notices, avec MMR `exploration=0` vs `100`. Il passe de 1 à 2,
  sans modification de l'identité des notices ou des sources.
- **Intégrité :** clés de Notice C non inventées, variantes d'origine
  `provider/credential/docbase/collection/record` conservées à l'identique,
  édition possédée exclue sur ISBN réellement validé, préférence d'autre
  profil inapplicable, aucun droit/acquisition ou appel HTTP inféré.

## Critères et limites

Le test utilise le moteur de classement réel `rank_candidates_prepared`
et les politiques existantes, sans ajouter de pondération implicite depuis
`loan`, `owned`, `state` ou `source_provider`. Les étiquettes de vérité
sont explicitement **synthétiques** et posées avant calcul ; elles servent à
détecter une régression technique du score et de la MMR. Elles **ne prouvent
pas** un gain NDCG sur des lecteurs réels, une amélioration absolue de
pertinence, une qualité du catalogue en production ou une compatibilité avec
Notice C non encore livré. L'opt-in sauvegardé, la révocation de snapshot,
la performance et le rendu GTK restent couverts par leurs suites dédiées et
ne sont pas requalifiés ici comme « tests terrain ».

Cette tranche répond au besoin de **comparaison quantitative reproductible**
de #4681. Les évaluations humaines et la provenance consolidée requièrent
des contrats/preuves distincts ; elles ne bloquent pas la livraison de ces
assertions offline.
