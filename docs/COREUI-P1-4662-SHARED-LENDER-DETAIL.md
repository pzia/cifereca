# P1 #4662 — section de détail commune Recherche/Recommandations, sans action trompeuse

Le Core publie `cross_profile_borrow_detail_section(preview)` dans
`cifereca.core.borrow_for_identity_detail`. Le résultat est un
`DetailSection` du **contrat public `UiBookDetailPresentation`** ;
les rendus Recherche et Recommandations savent déjà présenter ce modèle,
sans nouvelle interface GTK ni copie d'un moteur d'emprunt.

## Trois modes d'affichage

- **0 offre** : « Éligibilité personnelle et offre exacte non vérifiées » ; jamais un faux « aucun droit global ».
- **1 offre propre ou d'un autre prêteur** : profil, nom de compte publiable et état du transfert ; le prêt reste **observé / à revalider**.
- **N offres** : une ligne distincte « Choix 1/N » pour chaque compte/édition qualifié, y compris deux comptes d'un même profil ; pas de sélection arbitraire.

Les libellés sont recomposés à partir de noms courts et non privés, et les
identifiants de profils, credentials, comptes, éditions, URLs, jetons et
`option_id` ne sont pas affichés. Les états de copie restent
« transfert bloqué » ou « transfert non vérifié » ; la section ne
contient **aucune `UiBookDetailAction`, aucun lien ni callback**.

`DetailPanel.show_item_detail(..., borrow_preview=None)` accepte désormais
la section en option et l'insère dans **la présentation standard** avant
rendu. Le comportement sans paramètre est inchangé et n'effectue aucun GET
fournisseur ou recherche d'observation supplémentaire. Recommandations
peut réutiliser le même `DetailSection` dans sa présentation existante.

## Limite explicite

Il ne s'agit **pas encore de l'UX cliquable de fermeture** : tant que
l'observation positive par fournisseur n'est pas branchée, les écrans actuels
n'appellent pas `show_item_detail` avec un tel preview. Le CTA
« Emprunter avec A » ou le menu N ne peuvent devenir actifs que lorsque le
Core dispose du contrat mutateur avec consentement, plan exact,
anti-rejeu et livraison/DRM contrôlée. Afficher une option consultative
comme un bouton exécutable serait trompeur.

Tests `test_core_ui_p1_4662_shared_lender_detail_section.py` :
0/1/N, profil propre, prêteur étranger, deux comptes identiques en
libellé, confidentialité, refus des previews falsifiés et port opt-in.
