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

Le test `test_actual_v2_css_text_colours_remain_accessible_across_surfaces`
calcule désormais les rapports **à partir des déclarations du CSS livré**, et
non des seules valeurs figées du tableau. Il couvre aussi le panneau latéral,
la sélection, les libellés secondaires sélectionnés, les badges de contenu,
les boutons principaux et les libellés secondaires sur fenêtre, carte et
survol. Le contour de focus explicite est contrôlé à **3:1** au minimum
contre les fonds clairs déclarés (`test_actual_v2_focus_ring_contrasts_with_light_surfaces`).
Un changement de palette défavorable fait ainsi échouer la CI, mais cette
vérification statique **n'atteste ni le dessin effectif ni la navigation au
clavier** : l'inspection privée des PNG GTK et du focus reste obligatoire.

Les libellés secondaires `dim-label` des lignes de navigation sélectionnées
prennent explicitement le blanc ; leur encre secondaire `#4e6482` n'atteint
pas le contraste WCAG AA sur l'accent bleu. Le contraste des libellés
secondaires clairs tient aussi compte de l'opacité native des labels activés.
Dans l'apparence sombre, les boutons suggérés et la sélection de navigation
activés utilisent la même paire bleue/blanche à 5,91:1, localement à la
fenêtre Cifereca. L'encre des boutons destructifs reste rouge et devient
plus claire. Les autres fonds sombres restent ceux d'Adwaita.

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

### Preuve d'apparence GTK au moment de chaque capture

Les 36 entrées `screen.json.screenshots` consignent maintenant séparément
l'apparence **demandée** et `observed_appearance` lue depuis
`Adw.StyleManager.get_default().get_dark()` **au moment précis du PNG**.
La classe CSS `cifereca-modern-light` de la vraie fenêtre GTK doit être
cohérente avec cette observation avant la capture. La revue privée refuse
toute discordance ou preuve manquante, **même si le rapport porte PASS et
que les octets PNG/empreintes SHA sont valides**.

Pour chaque vue, le validateur exige les attestations GTK
`P1_757_viewport_bounds_*`, `P1_757_dark_transition_*`,
`P1_757_light_restore_*`, les deux attestations du vrai Détail Recherche,
ainsi que l'hébergement des vrais plugins Accueil et Constellations lorsqu'ils
font partie du périmètre. L'ensemble est conditionné par
`P1_757_all_routes_light_dark_gtk`. Les preuves déjà produites sans
observation par capture doivent être **recapturées** sous le nouveau HEAD
dans l'espace privé ; une ancienne grille ne constitue plus une attestation.

Ceci améliore la traçabilité GTK, mais **ne remplace pas** l'inspection
visuelle en plein format, notamment hover, clipping, focus et contraste réel.

### Règle d'intégrité des images revues (15/11/2026)

La grille et son manifeste privé sont liés **aux octets des 36 PNG originaux**,
en plus du SHA du commit. Un nouveau lancement GTK sur le même commit peut
reproduire les mêmes étapes avec des pixels différents : l'ancienne grille
remplie **ne peut pas** valider les nouvelles captures. La régénération des
planches et la vérification du verdict refusent alors l'opération, sans
écraser les notes de l'inspection précédente. Archiver séparément l'ancienne
revue dans l'espace privé, puis créer une grille neuve pour ces nouveaux PNG ;
ne rien téléverser vers les artefacts CI publics.

Le manifeste privé lie également la grille au `probe_evidence_sha256`,
empreinte des **deux `screen.json` complets** après vérification des
assertions GTK. Modifier les observations du probe sur le même HEAD et
avec des PNG identiques invalide désormais une grille déjà remplie ;
un ancien manifeste sans cette empreinte est refusé et requiert une
nouvelle revue locale. L'empreinte du rapport ne remplace pas celle des
PNG : les deux sont obligatoires.

La valeur `capture_fingerprint_sha256` ne certifie pas l'accessibilité ni
l'absence d'écarts visuels. Elle lie uniquement les observations humaines à
la collection exacte des PNG vérifiés.

La lecture des entrées privées est également **fail-closed** :
les deux dossiers de captures doivent être de vrais répertoires (pas de
lien symbolique) ; les `screen.json` et les PNG ne peuvent être ni des
liens symboliques, ni des fichiers à plusieurs liens matériels, ni
dépasser respectivement 4 Mio et 40 Mio. Ces exigences s'appliquent à
la génération des planches **et** à la validation d'une grille déjà remplie.
Un fichier extérieur, même si ses octets et son SHA semblent conformes,
ne peut servir de preuve UX.



### Verdict de la grille privée (P1 #757)

Le script **ne remplit pas** les observations UX à la place du reviewer.
Après inspection des **PNG originaux en pleine résolution**, renseigner dans
`modern-light-private-review/ux-checklist.csv` les **36 lignes** (9 vues,
2 apparences, 2 largeurs). Pour chaque axe
`readability_contrast / keyboard_focus / clipping /
busy_disabled_error_states / density`, indiquer `OK`, `KO` ou `N/A`.
Une valeur `N/A` exige une justification en notes privées et **n'est pas un
PASS** ; un `KO` implique `ux_verdict=KO` pour la ligne.
**Chaque ligne avec `ux_verdict=KO` exige aussi un diagnostic concret** dans
`notes`, conservé dans le CSV privé (jamais dans le reçu agrégé, les logs
ou GitHub). Un simple verdict négatif sans commentaire ne suffit pas à
qualifier l'écart ni à le transmettre à CoreUI/UX. Un `KO` global reste
possible même si les cinq axes sont renseignés `OK`, à condition de
consigner la raison indépendante de ces axes dans les notes.
Les états non observés ne doivent pas être marqués `OK`.

```bash
python3 e2e/modern_light_review.py --root e2e-artifacts --verify-checklist
```

La commande vérifie **de nouveau** les PNG originaux (SHA-256, format,
résolution, pair clair/sombre), l'identité du HEAD et du manifeste, puis la
complétude et l'absence de doublons/contradictions dans les 36 observations.
Elle dépose **localement seulement**, en mode `0600`, le fichier
`visual-review-verdict.json` : SHA du commit, empreinte de la grille, nombre
de lignes, anomalies et exclusions. Le contenu des notes et les PNG ne
sortent pas de l'hôte de revue.

`ready_for_ux_decision=true` signifie uniquement que le reviewer a rempli
toutes les lignes sans `KO` ni `N/A`, **pas** qu'un test d'images aurait
certifié le contraste, les états ou le thème. La décision de fermer #757
nécessite toujours une revue UX effective, et la traçabilité de cette
décision distincte. Un run CI vert ou une grille vide ne peuvent pas
produire ce verdict.

