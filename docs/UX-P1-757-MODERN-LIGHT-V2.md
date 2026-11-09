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
n'est redéfini. L'overlay utilise une priorité de fournisseur CSS
`APPLICATION + 1`, pour survivre aux réinstallations du CSS CoreUI
(`APPLICATION`) sans affecter les fenêtres hors classe V2. À la fermeture de chaque fenêtre,
le fournisseur V2 de cette fenêtre et son abonnement au changement d'apparence
sont libérés ; une autre fenêtre encore ouverte conserve son propre overlay.
Les plugins qui embarquent des composants CoreUI héritent
de la même règle ; aucune exception `provider_id`.

Contrastes calculés (WCAG texte normal, seuil 4,5:1) :

| Usage | Avant-plan / arrière-plan | Rapport |
| --- | --- | ---: |
| Bouton principal et sélection de navigation | `#ffffff` / `#1d5fcb` | 5,91 |
| Texte et titres | `#152641` / `#f7faff` | 14,49 |
| Texte secondaire | `#4e6482` / `#f7faff` | 5,79 |
| Libellé de tag | `#12315f` / `#e5eefc` | 11,02 |

Les libellés secondaires `dim-label` des lignes de navigation sélectionnées
prennent explicitement le blanc ; leur encre secondaire `#4e6482` n'atteint
pas le contraste WCAG AA sur l'accent bleu. Aucun état sombre n'est modifié.

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


## Recette GTK automatisée sur le HEAD de la PR (07/11/2026)

Le scénario versionné e2e/modern_light_theme_probe.py utilise une **vraie
MainWindow** avec un catalogue et une identité **synthétiques hors ligne** ;
il ne simule ni les widgets GTK ni l'application. La configuration et la base
SQLite utilisent exclusivement un répertoire temporaire dédié, détruit à la
fermeture de GTK. Les chemins auxiliaires `HOME`, `XDG_CONFIG_HOME`,
`XDG_DATA_HOME`, `XDG_CACHE_HOME` et `XDG_STATE_HOME` sont aussi redirigés
vers ce répertoire, et l'installation des dépendances plugins est
**strictement hors ligne**. Lancer cette recette sur un poste de travail ne
doit ni ouvrir ni modifier la bibliothèque, les comptes, les caches ou la
configuration Cifereca par défaut. Le runner natif l'exécute
sur les deux surfaces Xvfb (1366×900 et 1600×1000) :

    python3 e2e/run.py --scenario modern-light-1366
    python3 e2e/run.py --scenario modern-light-1600

Le scénario installe réellement le package livré `home-page` dans le stockage
temporaire, l'active via le gestionnaire de plugins et vérifie que la factory
Accueil provient bien de ce package (pas du fallback CoreUI). Il héberge aussi
la vraie page Constellations sur une fixture entièrement synthétique.
Le scénario visite Accueil, Recherche, Bibliothèque, Emprunts,
Synchronisation, Plugins et Paramètres, sélectionne une notice synthétique
en Recherche pour en ouvrir le Détail, force les apparences clair/sombre via
le véritable Adw.StyleManager, contrôle l'application de la classe CSS,
la présence d'une surface mappée et une différence de pixels observable
entre les deux apparences. Le probe échoue aussi si une page dépasse la largeur
allouée par la pile GTK, si la fenêtre déborde de l'écran virtuel ou si
une ligne de navigation mappée refuse le focus GTK (vérifié dans la
fenêtre réelle, et pas seulement par l'appel à grab_focus()).
Il capture chaque vue et chaque apparence dans
e2e-artifacts/modern-light-*/, avec les empreintes dans screen.json.

**Preuves :** les PNG restent sur l'hôte de recette, et sont **exclus des
artefacts GitHub Actions publics**. Le workflow E2E Smoke ne publie
que les verdicts expurgés. Il faut exécuter localement ces deux commandes
sur un hôte GTK autorisé et inspecter les PNG privés pour conclure
sur contraste réel, focus, densité, clipping, hover et messages d'état.
Le scénario valide désormais la **page Accueil d'un package réellement
installé en sandbox**, ainsi que Constellations : aucune installation ni
configuration du poste utilisateur n'est touchée. Il ne remplace toutefois
pas la revue humaine des captures de ces pages. Une variation moyenne des pixels
n'est pas une certification WCAG AA. Si le runner est vert, ce n'est
**toujours pas** une validation UX manuelle, ni une justification de
fusion automatique.


## Revue privée des captures réelles

Après les deux scénarios GTK PASS sur le même commit, exécuter sur l'hôte
habilité `python3 e2e/modern_light_review.py --root e2e-artifacts`.
La vérification refuse les PNG absents, réécrits ou d'un autre HEAD et génère
six planches clair/sombre et une grille `ux-checklist.csv` privée.
L'agent UX doit inspecter les PNG originaux en pleine résolution : la création
de planches n'est **pas** une validation visuelle, WCAG ou un droit de fusion.
Aucune capture réelle ni donnée de compte ne doit être publiée dans les CI.
