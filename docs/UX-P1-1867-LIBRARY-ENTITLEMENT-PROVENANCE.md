# P1 #1867 — type de droits dans le détail Bibliothèque

La section **Présence / Bibliothèques / Sources** peut contenir des liens
locaux et plusieurs droits distants pour une seule édition. Précédemment,
chaque droit était intitulé « Droit distant », y compris les achats et prêts
mélangés entre identités.

## Projection en lecture seule

Pour chaque `possession` déjà attestée par la projection du Core :

| Type exact | État exact | Libellé |
| --- | --- | --- |
| `owned` | `active` | Achat possédé en ligne |
| `owned` | `archived` | Achat archivé |
| `loan` | `active` | Prêt de bibliothèque actif |
| `loan` | `expired`, `returned`, `inactive` | Prêt de bibliothèque non actif |
| `owned` ou `loan` | non reconnu | Droit distant non qualifié |
| absent, autre | tout état | Droit distant |

Le nom du fournisseur seul n'atteste **jamais** un achat ou un prêt.
Le chemin de stockage local est conservé dans une ligne séparée, sans
assimiler possession distante et EPUB présent. Les doublons de provenance
restent regroupés, les différentes identités sont visibles.

Aucune requête réseau, action ou synchronisation ne se déclenche depuis ce
rendu ; aucun droit, délai, prêt, copie ni fichier n'est créé ou modifié.
La présence de multiples provenances n'accorde pas de permission d'action
sur un prêt ambigu.

## Recette

`tests/test_core_ui_p1_1867_library_possession_provenance.py` :
types/états négatifs, corruption de champs, historique legacy, mélange
Kobo + Numilog + Paris, deux identités, achats archivés, prêt actif, placement
local, suppression des seules entrées strictement identiques.

La recette utilisateur réelle multi-comptes du ticket #1867 demeure ouverte.
