# Numilog P0 #1072 — action utilisateur d'enrichissement (Core Search)

Ce jalon branche les opérations explicites existantes des plugins Numilog à
**une action du panneau détail de recherche**. Il ne crée pas de nouvelle
méthode d'acquisition, de synchronisation ou de session privée.

## Parcours

1. Core reçoit un `SearchResult` Numilog acheté (`provider=numilog`)
   ou commercial public (`provider=numilog-catalog`).
2. Le panneau affiche **« Enrichir depuis Numilog »** uniquement si la notice
   possède un lien public canonique plausible et une provenance compatible.
   L'affichage et la sélection ne déclenchent **aucune requête**.
3. Un **clic explicite** envoie une tâche `USER_ACTION` hors du thread GTK.
   L'identité active et le contexte de sélection sont figés.
4. Le Core atteste **avant tout accès** que la connexion Numilog privée
   appartient exactement à l'identité courante ; pour le catalogue anonyme,
   aucune connexion privée n'est consultée.
5. Le plugin vérifie ensuite lui-même : fraîcheur de `MesLivres`,
   correspondance de l'URL publique avec la carte produit et la fiche Book
   pour un achat ; provenance publique et absence de droits pour un résultat
   du catalogue.
6. Au retour, Core recontrôle l'identité et l'invariance du record, du compte,
   de la possession, des droits de téléchargement et du format.
   Les résultats d'une sélection ou identité périmée sont abandonnés.
7. La nouvelle projection est rendue dans le détail **sans modifier la
   bibliothèque persistée, le cache des acquisitions, le fichier local ou les
   droits**. Tout échec se traduit par un message générique sans exposer des
   URL signées, identifiants de compte ou exceptions des fournisseurs.

## Limites et responsabilités

- Cette première action est limitée aux **résultats de recherche distants**,
  après leur disponibilité. Le panneau de **Bibliothèque locale** ne possède
  pas encore d'action Numilog car une ligne locale peut agréger plusieurs
  comptes/identités : l'utiliser comme preuve de propriété serait incorrect.
- Le statut des disponibilités demeure celui du dernier appel `holdings`.
  La projection de métadonnées n'est jamais utilisée comme preuve de droits
  ou de l'existence d'un EPUB.
- Le singleton public reste désactivé par défaut tant que le scraping réel,
  robots/CGU, pages suivantes et DOM de produit ne sont pas qualifiés.
- Aucun catalogue HTML ou appel réseau réel n'est testé dans cet incrément.
  Les tests Core et CoreUI sont synthétiques, sans GTK ni données privées.
- La **revue Core / CoreUI** est requise pour confirmer l'intégration dans
  les différents parcours de résultats et la politique de navigation
  multi-identités, avant fusion par CICD–Merger.

Références : #1072, #1886, #1905.
