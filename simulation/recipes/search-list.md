# Recette UX — liste de Recherche

Recette **très ciblée** sur la liste de la page **Recherche**. Elle ne valide ni
les providers distants, ni les détails livre, ni les autres pages.

## But

Reproduire de façon déterministe les défauts de géométrie de la liste :

- bord gauche/largeur interne anormale ou bande blanche ;
- cellule qui impose une largeur supérieure à la liste ;
- titre très long ou sans espaces ;
- auteur/facette extrêmement long ;
- nombreux auteurs/facettes sur une même ligne de résultat ;
- comportement après scroll/recyclage des cellules.

La requête commune est `UXSTRESS`.

Pour isoler le **master-detail avec un livre normal**, utiliser la sous-recette
`--query "UXSTRESS normal"`. Elle ne retourne que la notice témoin
`UXSTRESS contrôle normal` / `Zoé Martin` et permet de distinguer les défauts
de layout de ceux provoqués par les facettes pathologiques.

## Données

Le profil `ux-search-list` contient seulement **12 EPUB locaux**. Après le
`rebuild`, remplacer les EPUB génériques par la fixture de stress :

```bash
python3 simulation/datasetctl.py rebuild ux-search-list
python3 simulation/recipes/search_list_fixture.py
python3 simulation/datasetctl.py summary ux-search-list
```

La fixture couvre notamment :

- titre long avec espaces ;
- titre de plus de 180 caractères sans espace ;
- auteur unique extrêmement long ;
- 10 auteurs ;
- 10 auteurs longs ;
- Unicode ;
- valeurs auteur à 24 et 25 caractères pour vérifier la borne de facette ;
- combinaison titre long + 10 auteurs ;
- ligne témoin normale.

Le manifeste exact est écrit dans :

```text
simulation/.runtime/ux-search-list/search-list-fixture.json
```

ou sous `$CIFERECA_DATASET_RUNTIME/ux-search-list/` si le runtime est déplacé.

## Indexer la bibliothèque

Avec le harnais Debian 13 déjà bootstrappé :

```bash
export CIFERECA_GTK_TEST_HARNESS="$PWD/environnements/openai-chat-debian"

python3 simulation/datasetctl.py exec ux-search-list -- \
  env PYTHONPATH="$PWD/src" \
  "$CIFERECA_GTK_TEST_HARNESS/gtk-python" \
  -m cifereca.cli catalog-scan
```

## Captures ciblées

Exécuter le même scénario en **760, 1024 et 1300 px**. Le scénario utilise le
raccourci canonique `Ctrl+F` pour ouvrir Recherche, saisit `UXSTRESS`, lance
la recherche locale puis capture avant et après scroll.

Depuis la racine du dépôt :

```bash
export CIFERECA_GTK_TEST_HARNESS="$PWD/environnements/openai-chat-debian"
mkdir -p evidence/search-list

for width in 760 1024 1300; do
  python3 simulation/datasetctl.py exec ux-search-list -- \
    env \
      PYTHONPATH="$PWD/src" \
      CIFERECA_GTK_TEST_HARNESS="$CIFERECA_GTK_TEST_HARNESS" \
    "$CIFERECA_GTK_TEST_HARNESS/headless" \
      --screen "${width}x720x24" -- \
    "$CIFERECA_GTK_TEST_HARNESS/gtk-python" \
      "$PWD/simulation/recipes/search_list_capture.py" \
      --window-width "$width" \
      --window-height 720 \
      --out-dir "$PWD/evidence/search-list/$width" \
      --cwd "$PWD" -- \
    "$CIFERECA_GTK_TEST_HARNESS/run-cifereca" \
      --no-plugins \
      --force-plugin core.ui.pages \
      --force-plugin core.ui.widgets \
      --force-plugin core.ui.titlebar \
      --force-plugin core.ui.system \
      --verbose
done
```

Chaque largeur produit :

```text
evidence/search-list/<largeur>/
  01-search-list.png
  02-selected-detail.png
  03-search-list-scrolled.png
  report.json
  cifereca.stdout.log
  cifereca.stderr.log
```

## Critères de recette

La recette est **verte** seulement si, pour les trois largeurs :

1. la liste est visible, sans bande blanche ni décalage anormal vers la gauche ;
2. le bord gauche des cellules reste aligné avec celui de la liste ;
3. aucun titre, auteur, badge ou bouton n'impose une largeur supérieure à la cellule ;
4. le titre reste borné à deux lignes et son contenu complet reste accessible par tooltip ;
5. les facettes longues sont affichées sous forme compacte/ellipsée, sans agrandir la liste ;
6. la ligne à 10 auteurs et celle à 10 auteurs longs restent contenues horizontalement ;
7. après scroll, les cellules recyclées ne conservent ni facettes ni callbacks d'une autre ligne ;
8. à 1024 px et plus, sélectionner un livre conserve un master-detail côte à côte : le détail apparaît sans faire disparaître la liste ;
9. le mode liste/détail exclusif n'est accepté qu'aux vraies largeurs étroites ;
10. la fenêtre Cifereca est encore vivante au moment des captures.

Pour les facettes, la borne actuellement déclarée par le Core est
`LIST_FACET_MAX_CHARS = 24`. Le cas 24 caractères doit rester entier ; le cas
25 caractères doit montrer une ellipse sans élargir la cellule.

## Hors périmètre

Cette recette ne valide pas :

- le placement des filtres Recherche/Bibliothèque ;
- les recherches distantes ;
- le détail livre ;
- les performances Recommendations ;
- la mise en page Bibliothèque.

Ces sujets doivent avoir leurs propres recettes afin que cette preuve reste
lisible et attribuable.
