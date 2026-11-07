# P1 #3687 — Compteur de résultats masqués dans la liste partagée

## Défaut

`MasterDetailPage._update_count()` effaçait systématiquement le compteur
dès que le modèle GTK contenait zéro ligne. Lorsqu'un filtre local masque
toutes les recommandations ou tous les documents d'une bibliothèque non vide,
le composant ne distinguait donc pas **0 visible sur N** de **aucun document
chargé**. Les autres éléments du panneau (« Aucun résultat pour ces filtres »)
ne compensaient pas cette perte d'information dans l'en-tête.

## Comportement livré

- **0 visible, N attesté > 0** : `0 / N élément(s)` dans la liste commune,
  avec la flexion singulier/pluriel issue de `_item_count_label`.
- **0 visible, 0 attesté** : compteur vide ; aucune population inventée.
- **N visible > 0** : libellés historiques conservés, notamment le rapport
  `X / N` si `X < N`.
- **Mode métier total seulement** (Historique) : si un total N est attesté
  mais le premier lot GTK est encore vide, afficher uniquement le total N,
  sans prendre 0 lignes matérialisées pour une quantité métier.
- `clear_rows` et la remise à zéro du total sont inchangés : une nouvelle
  identité ou un nouveau résultat vide ne conserve aucun compteur obsolète.

Les Recommandations transmettent déjà `total_count=len(self._ranked)` à
`MasterDetailPage.show_rows`. Cette tranche ne recalcule pas le classement,
n'inspecte pas d'autre profil et ne convertit pas un catalogue en droit.
Recherche conserve son compteur métier `Connu/Étendu`, sans renommer les
états de fournisseurs.

## Recette

- `tests/test_core_ui_357_library_count.py` : 0/0, 0/1, 0/20, retour
  à 2/3 puis 3/3, changement de résultat, mode total seul.
- `e2e/search_filter_probe.py` : vraie `Gtk.Label` sous Xvfb et méthode
  commune de rendu, vérification du total affiché à zéro puis retiré.
- `e2e/recommendations_detail_probe.py` : vrai panneau Recommandations,
  champ Titre ne correspondant à aucun des trois livres, contrôle conjoint
  du compteur local `0 sur 3` et du compteur de liste `0 / 3 éléments`.
  La commande GTK « Effacer les filtres » restitue trois éléments et
  `3 élément(s)`, sans accroître le numéro de génération ni rechercher un fournisseur.
- Qualifier ensuite dans #774 la largeur GTK **effective** 1024/1366/1600,
  la navigation clavier/focus complète et la juxtaposition des deux pages.
