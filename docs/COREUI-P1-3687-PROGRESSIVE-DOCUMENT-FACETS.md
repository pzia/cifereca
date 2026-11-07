# P1 #3687 — Sélections Format et Support pendant le classement progressif

## Défaut

Un lot provisoire de Recommandations ne présente qu'une tête de résultats.
Lorsqu'une source sélectionnée disparaissait temporairement de cette tête,
les facettes **Format** et **Support** étaient reconstruites comme si la
réponse était complète. Le choix de l'utilisateur était alors réinitialisé
sur « Tous », ce qui pouvait faire apparaître des notices auparavant filtrées.
Les facettes Source (#4906) et Langue (#4907) disposaient déjà d'une garde
équivalente, pas les facettes documentaires.

## Contrat corrigé

- Une projection **complète** atteste les supports et formats disponibles.
- Lors d'une **projection provisoire**, seul le support déjà sélectionné et
  préalablement attesté, ainsi que les formats déjà sélectionnés et attestés,
  sont conservés même s'ils sont absents de la tête courante.
- Aucun nouveau choix non attesté n'est inventé. Les autres choix obsolètes
  ne sont pas retenus.
- Un résultat **final**, une erreur ou le changement de profil purgent les
  attestations réellement absentes, avec remise à zéro silencieuse des
  sélections correspondantes.
- Ce filtre est strictement local : aucune recherche fournisseur, droit,
  acquisition ou action n'est déclenchée par le recalcul des facettes.

## Preuves

`tests/test_query_controls_batching.py` vérifie les deux facettes ensemble :
sélections Physique + PDF sur un résultat complet, lot provisoire Numérique +
EPUB, conservation des choix et non-exposition des lignes non assorties,
puis abandon des choix absents en résultat final. Le changement de profil
réinitialise entièrement le témoin. Une garde AST verrouille le seul appel
`provisional=True` dans la méthode de publication des lots Recommandations.

La recette GTK/XTEST et la validation fournisseur réel demeurent les
critères de fermeture des tickets #3687 et #774.
