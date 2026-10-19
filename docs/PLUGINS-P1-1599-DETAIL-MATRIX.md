# Plugins P1 #1599 — conformité du détail Recommandations (19 octobre 2026)

Parent : [#1596](https://github.com/pzia/cifereca/issues/1596). Contrat public
CoreUI : [#1598](https://github.com/pzia/cifereca/issues/1598) et
`docs/COREUI-P1-1598-DETAIL-MATRIX.md`.

**Périmètre P1 strict : le seul plugin actif Recommandations.**
Jeunesse a été retiré du code (#741 P3, PR #2318) ;
Constellations est désactivé par défaut et suivi en #700 P2
(avec #1042, #706 et #705 selon responsabilités). Aucun de ces deux volets
ne bloque la sortie de #1599 ; ne pas réintroduire de rendu ni de source
Jeunesse et ne pas créer de second volet Constellations ici.

La [matrice détaillée, tenue dans le plugin](../src/plugins/recommendations/DETAIL-CONTRACT-1599.md)
est la référence pour la projection et les limites. La présente vue
transversale facilite sa comparaison avec Recherche/Bibliothèque.

## Comparaison stricte avec Recherche et Bibliothèque

| Contrat de détail | Recherche / Bibliothèque (#1598) | Recommandations (#1599) |
| --- | --- | --- |
| Rendu | `UiBookDetailPresentation` → `DetailPanel` du CoreUI | `host.build_book_master_detail` → `UiBookListEntry` et `UiBookDetailPresentation` → **même** `DetailPanel` ; aucun import `cifereca.gui` |
| Identité bibliographique | Notice sélectionnée / livre local, champs à provenance explicite | Candidat classé ; titre/plusieurs auteurs/description uniquement si chaînes publiques sûres ; variantes sources distinctes si coordonnées attestées |
| Métadonnées bibliographiques | Auteurs, support, langue, date, couverture, notice et identifiants prouvés | Champs auteurs indépendants `kind=author`, langues attestées `kind=language`, format/publication facultatifs, couverture HTTP(S) publique non signée, description facultative |
| Sections spécifiques | « Essentiel » / « Bibliothèque » / disponibilité, prêt et acquisition **selon origine et preuve** | « Recommandation » (affinité entière 0–100, classement diversifié, raisons attestées), « Catalogue » (sources indépendantes), « Notice », « Éditions et sources distinctes » ; LLM facultatif étiqueté **généré, non attesté** |
| Liens/actions | Navigation et droits décidés par le Core/les comptes | « Voir en ligne » seulement pour `friendly_url` ou `notice_url` vérifiée ; recherche ISBN **locale et explicite** d'une édition vérifiée ; jamais prêt, achat ni acquisition à partir d'une proximité |
| Profil / sélection | Identité exacte de la notice, pas de repli vers le compte courant | Capturer `identity_id` et générations de calcul/sélection ; invalider les actions après A→B→A, re-filtrage, disparition du profil, désactivation ou callback tardif |

**Divergences légitimes :** aucun champ disponibilité, `owned`,
exemplaire, statut de prêt, chemin local ou état d'acquisition ne doit
apparaître dans Recommandations faute de droit attesté. Les sections métier
ne sont pas une divergence de renderer. #1354 (convergence métier des actions)
reste hors #1599.

## Preuves disponibles et limites

- **Code :** `src/plugins/recommendations/recommendations.py` produit
  `UiBookDetailPresentation`, `DetailField`, `DetailSection` et
  `UiBookDetailAction` exclusivement via les API publiques.
- **Headless :** `test_recommendation_detail_projection_contract_p1.py`,
  `test_recommendation_catalogue_navigation_p1.py`,
  `test_recommendation_ranked_provenance_public_p1.py`,
  `test_recommendation_identity_switch_while_busy_p1.py`,
  `test_recommendations_provider_filter_public_p1.py` ;
  le durcissement de la reformulation LLM et ses cas hostiles sont suivis
  dans la PR #2400. Ces tests ne valent pas recette GTK réelle.
- **Probe GTK préparé :** `e2e/recommendations_detail_probe.py` utilise
  GTK4/XTEST réels avec candidats **synthétiques** et hôte public réel ;
  couvre sélection, édition, clic clavier ISBN local, action périmée,
  liste vide, changement de profil et désactivation. Le probe ne démontre
  ni disponibilité d'un provider distant ni bon fonctionnement sur un poste
  utilisateur.

## Gates encore nécessaires pour fermer #1599

1. Vérifier les suites plugins et les scénarios GTK **1366 et 1600 px**
   sur le SHA **final fusionné**, avec preuves de focus/clavier/scroll/retour,
   (dés)activation, identité A→B, callbacks tardifs et 0/1/N.
2. Comparer les champs communs effectivement rendus par
   Recherche/Bibliothèque au contrat #1598 fusionné ; conserver les
   sections métier et signaler toute lacune de l'API publique au CoreUI.
3. Vérifier install/packaging/E2E et la confidentialité : aucun
   URL signé, compte, donnée arbitraire ou droit inventé ; aucune
   acquisition ni requête LLM sans consentement explicite.

**Ne pas fermer automatiquement** #1599 ni #1596 sur la seule base de cette
matrice ou des tests hors réseau. Les anciennes mentions de PR #1798 et
#1800 « non fusionnées » dataient du 15/10 : elles ont depuis été fusionnées ;
le volet Constellations associé à #1800 n'est plus P1.
