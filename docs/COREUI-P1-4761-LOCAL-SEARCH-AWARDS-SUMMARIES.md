# P1 #4761 — Parité du champ global : prix littéraires et résumés

## Écart résiduel après #6178 / #6184 / #6185

`src/cifereca/core/search.py::local_search_evidence` considère, pour
l'interrogation **tous les champs**, les prix littéraires déjà indexés
(`awards`, `prizes`, `literary_prizes`), les descriptions et résumés
bibliographiques (`description`, `summary`, `synopsis`) et l'éditeur
public. Le filtre local de Recommandations lisait sujet, série,
collection, langue et ISBN, mais pas ces trois groupes. Un **même objet
documentaire attesté**, présenté dans ces deux contextes, restait donc
filtrable différemment.

## Contrat de cette tranche

- Étendre uniquement le mode **Champ = Tous les champs**, à partir des
  valeurs de livre déjà en mémoire : prix littéraires, description,
  résumé, synopsis et éditeur public d'un candidat ou de ses métadonnées
  projetées. Les champs explicites déjà disponibles sont inchangés.
- Conserver la sémantique de recherche multi-termes et des phrases de
  #6184, l'ordre diversifié du classement, et le garde ISBN source-exact.
  **Un terme d'un seul caractère doit constituer un mot entier** : indexer
  un résumé « Description publique » ne doit pas faire correspondre
  `Livre B` à `Livre A` simplement grâce au « b » intérieur de
  « publique ». C'est le garde déjà pratiqué par le champ `Sujet` ;
  les expressions entre guillemets restent strictes dans un seul champ.
- Les champs proviennent des clés publiques connues et de valeurs
  scalaires attestées. Ne jamais convertir un objet fournisseur, un
  dictionnaire arbitraire, un chemin ou une URL d'acquisition en texte
  recherchable. Chaque source est limitée à 32 valeurs et chaque valeur
  au plus 512 caractères dans la recherche large ; les champs publics
  malformés ou surdimensionnés sont omis conservativement.
- Même un texte bibliographique inattendu qui contient une URL, une
  signature ou un marqueur de credential ne devient pas recherchable.
  Cette règle s'applique aux catégories nouvellement couvertes comme
  aux catégories déjà indexées par le mode Tous.
- Aucun nouveau GET, POST, accès fichiers, LLM, recalcul, transfert de
  droits ni interaction fournisseur.

## Preuve et limites

Les tests Plugin donnent **exactement le même objet documentaire
normalisé** à la recherche locale Core et au filtre local Recommandations,
puis vérifient les correspondances positives sur awards, summary,
description, synopsis, publisher, auteurs et sujets, y compris termes
répartis entre les champs. Les cas hostiles incluent objet opaque, URL
signée, token et chemin de fichiers.

Cette preuve porte sur les **deux contrats de filtrage en mémoire**.
Ce n'est **pas** la comparaison visuelle d'une même édition/source dans
les deux vraies pages GTK : les captures et la revue UX restent #774 et
les couvertures transverses #3104/#6168. Ne pas clore #4761 sur cette
tranche seule ; pas de PU nouveau requis.
