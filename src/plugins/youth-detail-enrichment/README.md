# Jeunesse — enrichissement des fiches

Contribution `book.detail.enrichment` pour #741.

Le plugin lit exclusivement le service public `catalog.youth` déjà activé et son cache courant. Il ne contient aucun client HTTP, ne déclenche aucune collecte et ne scanne aucun fichier.

## Rattachement strict

Une fiche standard n'est enrichie que si une notice Jeunesse en cache correspond par preuve forte :

1. ISBN-10/13 valide identique ;
2. même provider + identifiant source ;
3. URL de notice canonique identique.

Le moteur moderne utilise `find_cached()` et ne dépend donc plus du titre pour préfiltrer les notices. ISBN, provider+identifiant ou URL de notice sont transmis directement au cache. Le titre+auteur ne suffit **jamais** à afficher un âge. En cas d'ambiguïté entre plusieurs meilleures notices, aucune contribution n'est rendue.

## Présentation

La section « Jeunesse » conserve pour chaque tranche :

- type d'âge ;
- bornes ;
- libellé source original ;
- source ;
- confiance lorsqu'elle est disponible.

Une action « Voir la source Jeunesse » n'est ajoutée que si la notice cache possède une URL HTTP(S) publique. Le Core applique en plus ses propres garde-fous.

## Performance

`cached_detail()` appelle en priorité `catalog.youth.find_cached()` avec un maximum de 20 candidats et uniquement des identifiants forts. Un fallback `search_cached()` reste prévu pour compatibilité avec un ancien moteur. Aucun de ces chemins ne déclenche de réseau.
