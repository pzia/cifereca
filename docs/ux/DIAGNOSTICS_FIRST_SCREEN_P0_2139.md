# UX P0 #2139 — comparaison privée du premier écran Diagnostics

## Contexte et preuve

Le parcours exploratoire `e2e/diagnostics_page_probe.py`, enrichi dans
[#2221](https://github.com/pzia/cifereca/pull/2221), enregistre la géométrie
réelle des contrôles Diagnostics à 1366/1600 px et après une demande de fenêtre
**1280×850**, avec brokers synthétiques et rendu GTK4/X11. Ces observations
figurent uniquement dans `screen.json.diagnostics_entry_ux` **privé** :
les résumés E2E publics demeurent limités à des verdicts expurgés selon #1143.

Le travail produit [#2239](https://github.com/pzia/cifereca/pull/2239)
place Journaux dans un onglet initial, séparé de Capture, État et Rapports ;
[#2241](https://github.com/pzia/cifereca/pull/2241) rationalise l'entrée dans
Paramètres. Ces PR nécessitent une comparaison avant/après **sur la même
fixture et avec les mêmes tailles**, pas une simple assertion de présence
d'un onglet.

## Mode opératoire local

Suivre `environnements/E2E-UX.md`, exécuter un scénario réel GTK du plugin
via le harnais existant, puis analyser le `screen.json` **sur la machine
de recette**, sans téléverser ni publier le fichier :

```bash
python3 e2e/diagnostics_entry_review.py /chemin/prive/screen.json
python3 e2e/diagnostics_entry_review.py /chemin/apres/screen.json \
  --before /chemin/avant/screen.json
python3 -m unittest discover -s e2e -p 'test_diagnostics_entry_review.py' -v
```

Le script accepte uniquement un rapport terminé en `PASS`, produit par
`GTK4/X11`, `ui.page.diagnostics` et le broker de simulation bornée.
Il vérifie une **allowlist fixe** de onze identifiants fonctionnels :
titres système/journaux/IA, filtres de source/sévérité/période/module/texte,
boutons erreurs/tous/copier. Il ne renvoie ni textes GTK, ni valeurs
d'événements, ni chemins, ni copies d'erreurs ou captures.

La sortie fournit les dimensions de fenêtre/viewport réellement allouées,
l'étendue du scroll, l'état `present` vs `allocated`, la visibilité
**entière** et l'intersection du premier viewport, le pourcentage vertical
visible et la présence de découpes horizontales. La comparaison `--before`
restitue uniquement les transitions des mêmes identifiants et le changement
du nombre de **cinq filtres** entièrement visibles. Elle refuse les deltas
si les dimensions sont trop différentes (24 px largeur fenêtre, 48 px
hauteur fenêtre, 32 px largeur viewport, 48 px hauteur viewport), ou si
la mesure d'une étape manque. Les motifs d'échec sont constants : aucune
exception ni valeur du rapport privé n'est affichée.

## Qualification de la recette

- **Cible principale : 1280–1366 px**, police normale puis grande ; 760/1600
  restent des vérifications de non-régression. Le contrôle
  `target_width_reached` évite de confondre 1280 px demandés et une
  fenêtre empêchée de rétrécir par ses minima GTK.
- Comparer les mêmes 65 événements synthétiques, source et versions de
  fixture ; une hausse de visibilité ne prouve **ni** la lisibilité ni
  la qualité de la hiérarchie. Un titre présent mais hors écran n'est
  pas équivalent à un contrôle absent.
- Confirmer visuellement en fenêtre réelle : « Journaux » doit être
  reconnaissable dès l'ouverture, filtres puis sélection des erreurs
  accessibles, et Capture/Export explicitement distincts. Vérifier
  l'absence de capture ou d'analyse IA automatiques.
- Compléter par Tab/Shift+Tab, vérification d'un JSON exporté et recette
  Orca/AT-SPI et multi-moniteur (#1313) ; **le script ne les couvre pas**.
- Même avec un `PASS` CI, ne clore #2139 qu'après correctifs produit et
  recette UX représentative. Seuls CoreUI/Plugins corrigent l'IHM.

**Limite matérielle :** en environnement local sans `gi`, ces tests
synthétiques n'exécutent pas GTK. L'analyse effective exige le rapport
réel privé capturé par le harnais GTK/Xvfb. Ne pas contourner la
confidentialité en publiant `screen.json`.
