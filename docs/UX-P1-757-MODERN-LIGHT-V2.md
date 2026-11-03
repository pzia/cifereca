# P1 #757 — GTK4 « Moderne & clair » V2 (option A)

**Décision produit :** 03/11/2026, option A. Le choix ne valide pas les anciens
screenshots ni ne demande un thème clair forcé à tous les utilisateurs.

## Provenance et intégration

Prototype de travail non fusionné :
`ux/gtk-css-style-study-20261008:ux_style_lab/01-clair-v2.css`.
La palette est reprise, **pas** son stylesheet global :

- Accent bleu : `#1d5fcb` ; fenêtre : `#f7faff`.
- Encre : `#152641` ; barre latérale : `#e9f1fb` ;
  carte : `#ffffff` ; bord : `#bfd0e8`.
- Texte secondaire : `#4e6482` ; tags : `#12315f` sur `#e5eefc`.

Le composant central `gui/modern_light_theme.py` ajoute un overlay GTK4
**uniquement** sous `window.cifereca-modern-light`.
Le propriétaire de la fenêtre suit `Adw.StyleManager.get_dark()` et retire
cette classe au passage en sombre, sans changer la préférence d'apparence.
Si l'état est indisponible ou non booléen, aucune coloration claire n'est
présumée. Aucun token public Adwaita `accent/error/warning/success`
n'est redéfini. Les plugins qui embarquent des composants CoreUI héritent
de la même règle ; aucune exception `provider_id`.

Contrastes calculés (WCAG texte normal, seuil 4,5:1) :

| Usage | Avant-plan / arrière-plan | Rapport |
| --- | --- | ---: |
| Bouton principal et sélection de navigation | `#ffffff` / `#1d5fcb` | 5,91 |
| Texte et titres | `#152641` / `#f7faff` | 14,49 |
| Texte secondaire | `#4e6482` / `#f7faff` | 5,79 |
| Libellé de tag | `#12315f` / `#e5eefc` | 11,02 |

Ces rapports **ne remplacent pas** un audit du rendu effectif GTK :
transparences, disabled, hover, support CSS, mise en page et widgets tiers
restent à observer en situation réelle.

## Recette obligatoire (non encore acquise)

Capturer **l'application GTK réelle** aux largeurs **1366 et 1600 px**, avec
les mêmes scénarios d'identité et des fournisseurs fictifs/synthétiques
autorisés, sur Accueil, Recherche et fiche Détail, Bibliothèque, Emprunts,
Synchronisation, Paramètres et au moins une page plugin. Conserver les
captures dans le dispositif privé de revue UX du dépôt, **pas** dans les
artefacts publics CI qui excluent images, identifiants de compte et pages
personnelles. Les captures de l'étude d'octobre ne sont plus disponibles ;
elles ne peuvent pas constituer une réception visuelle actuelle.

Contrôles visuels précis : sélection de source et de livre, focus clavier,
hover, boutons suggérés/destructifs, erreurs et états « partiel » / « en
cours » / « indisponible », listage dense (titre long + métadonnées),
contraste des formes désactivées, défilement des cartes Sync et Détail,
absence de masquage des actions à 1366 px, mode sombre puis retour clair.

**Déploiement :** aucune activation distante ni migration de configuration.
La mise en production est distincte de la validation CI et de la recette UX.
