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

## Ordre et style déclarés

Deux champs facultatifs supplémentaires utilisent le tri et les styles natifs :
`priority: int | None = None` et `destructive: bool = False`. Une priorité
entière (hors booléen) est transmise au tri stable déjà existant : une valeur
plus petite précède une plus grande, les égalités gardent l'ordre d'arrivée.
`None` ou une valeur mal typée conserve l'ordre historique ; le cache conserve
sa priorité historique 35 sans déclaration entière.

Le style `destructive-action` ne donne aucune permission. Une action
destructive ne reçoit jamais `suggested-action`, même si sa priorité est basse.
Les boutons sont déplacés tels quels dans le débordement, en conservant style,
état, motif et callback. Les deux collecteurs conservent ces déclarations.
Le producteur publie une nouvelle présentation pour modifier ces propriétés.

Les signaux d'activation vérifient aussi le marqueur d'état : un signal
retardé ou émis explicitement sur un bouton insensible n'exécute ni callback
ni lien. La révocation existante d'une ancienne présentation reste prioritaire
et une présentation suivante ne réactive jamais ses anciens boutons.

Cet état de présentation n'est pas une admission. Les callbacks conservent
leurs contrôles Core/plugins de source, compte, édition et permissions au clic.
Aucun appel fournisseur, nouveau droit ou mutation n'est déclenché au rendu.

Réception partielle de #3685 sous #6366 : états et raisons sur les détails
plugins/cache, ordre et style destructif déclaratifs. La migration des autres
actions natives, les placements et la réception globale des six vues restent
à qualifier.
