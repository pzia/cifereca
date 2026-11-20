# États des actions du détail partagé

`UiBookDetailAction` garde ses cinq arguments historiques et ajoute deux champs
facultatifs : `state="available"` et `unavailable_reason=""`.

| État fourni | Rendu | Activation |
| --- | --- | --- |
| `available` (défaut) | Titre et icône existants | Callback ou lien existant |
| `unavailable` | Bouton insensible, infobulle « Indisponible » et motif | Refusée |
| `busy` | Bouton insensible, infobulle « En cours » et motif | Refusée |
| Autre valeur | Bouton insensible, infobulle « Indisponible » et motif | Refusée |

Le producteur évalue cet état et publie une nouvelle présentation pour le
modifier. Les adaptateurs `PublicBookMasterDetailView.show_detail` et
`DetailPanel.update_cached_book_details` appliquent le même helper. Les deux
collecteurs de cache préservent l'état et bornent le motif à 280 caractères.
Le titre accessible et le bouton restent ceux de l'action, y compris lorsque
`AdaptiveActionBar` déplace ce bouton dans son menu de débordement.

Les signaux d'activation vérifient aussi le marqueur d'état : un signal
retardé ou émis explicitement sur un bouton insensible n'exécute ni callback
ni lien. La révocation existante d'une ancienne présentation reste prioritaire
et une présentation suivante ne réactive jamais ses anciens boutons.

Cet état de présentation n'est pas une admission. Les callbacks conservent
leurs contrôles Core/plugins de source, compte, édition et permissions au clic.
Aucun appel fournisseur, nouveau droit ou mutation n'est déclenché au rendu.

Réception partielle de #3685 sous #6366 : états et raisons sur les détails
plugins/cache. La migration des autres actions natives, les descripteurs de
priorité/emplacement et la réception globale des six vues restent à qualifier.
