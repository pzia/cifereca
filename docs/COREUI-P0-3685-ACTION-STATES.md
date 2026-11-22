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
Un descripteur natif `unavailable` ou `busy` sans cible (URI vide et
callback `None`) reste visible dans un cache même si sa contribution ne
contient aucune section. Le bouton est insensible et aucun signal
d'activation n'est connecté. Une action disponible sans cible, un état
inconnu sans cible ou une URI invalide ne sont pas admis par les collecteurs.

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

## Inventaire des points de rendu et handoff CoreUI / Plugins (#3685)

| Parcours déjà présent | Port de rendu partagé | Source de l'action et garde |
| --- | --- | --- |
| Recherche, Bibliothèque, Emprunts, Historique, Récents | `MasterDetailPage`, `DetailPanel.show_presentation`, `AdaptiveActionBar` ; `BookListCell` pour les facettes | Le contrôleur de chaque parcours produit la présentation ; Core et le plugin revalident la source et les permissions au clic |
| Recommandations et pages plugin | `PublicBookMasterDetailView.show_detail` réutilise `MasterDetailPage` et `DetailPanel` | `UiBookDetailAction` et les callbacks typés déjà existants ; révocation sur génération, identité, epoch, route, page et mapping |
| Enrichissement/cache de détail | `DetailPanel.update_cached_book_details` ajoute des actions à la même barre | Les deux collecteurs conservent uniquement les états, priorités, motifs et cibles admissibles ; revalidation de contexte au clic |
| Lignes avec facettes auteur/texte | `BookListCell` | Protection de la clé exacte et de la génération de bind, y compris réutilisation du widget après A→B→A |

**Doublons écartés.** Le détail plugin n'installe ni second moteur de permissions ni nouvelle ABI d'action. La présentation de Recommandations passe par le port public du Core ; les panneaux de pages natives réutilisent les mêmes widgets et la barre d'actions. `UiBookDetailAction` conserve ses cinq champs historiques, enrichis uniquement de paramètres déclaratifs facultatifs. Une étiquette, une URL bibliographique ou un bouton sensible n'est jamais en soi une autorisation d'emprunt, d'achat ou de livraison.

**Points de révalidation.** `detail_action_state.py` traite les états et refuse une cible absente ; le contrôleur fournisseur ou Core conserve son droit de veto et son dispositif de confirmation. Les signaux natifs `activate-link` et les callbacks cache/plugin refusent les actions détachées, fermées, réaffectées à un autre détail, profil, service, source, route ou epoch. Les actions auteur/facettes de liste utilisent le bind courant. Un état `busy` ou `unavailable` ne déclenche jamais une action, même par émission synthétique de signal.

### Réceptions techniques réutilisées

- **États, motifs, priorité et débordement :** #6976, #6987, #6989, #7022, #7095 et #7414 ; même logique passive en plugin direct et cache, dont les cibles absentes/inadmissibles restent bloquées.
- **Clavier et focus natifs :** #7122, CI 44798530420 et GTK/XTEST 44798530433 verts. `book_detail_probe.py` couvre Tab, Espace/Entrée, Échap, saut d'action disabled et restitution du focus au menu ; les actions ne sont pas recréées lors du déplacement dans le débordement.
- **Révocation native :** #7416 sur les liens de liste, #7462 sur les actions de champ, #7530 sur `Gtk.LinkButton` du panneau ; checks d'identité/source/route complétés par #7540 (CI 44999703910, AST 44999703866, GTK 44999703858 verts après rejeu au HEAD exact) et #7550 (CI 45000390350, AST 45000390291, GTK 45000390369 verts), tous deux fusionnés et vérifiés sur `main`.
- **Accessibilité / dimensions :** les boutons gardent leur libellé accessible et leur priorité dans la barre native ; le probe GTK historique couvre la barre aux largeurs 1024 et 1366 et l'essai HiDPI, sans transformer une validation de clavier sur une page en preuve de tous les parcours.

**Handoff d'implémentation.** CoreUI maintient le renderer (`action_widgets.py`, `detail_panel.py`, `public_book_view.py`) et les gardes d'affichage ; Plugins fournit le descripteur et le callback déjà admis, sans exposer de jeton, session ou URL signée. Les décisions de capacité, comptes, crédits, droits, transactions, idempotence et confirmation restent dans Core/fournisseurs. Les projections particulières (Récents/Récupération, prêts, recommandations) sont des données de présentation, pas des moteurs d'action alternatifs.

**Limite de cette réception :** la recette obligatoire des **six pages en mode étroit** (froid/hors-ligne) est pilotée par #3684/#4980 via #7549. La CI d'un sink d'action et un GTK clavier sur une seule page ne suffisent pas à l'attester ; ce reçu reste distinct. Aucun périmètre supplémentaire de refonte graphique ni de droits n'est ajouté.
