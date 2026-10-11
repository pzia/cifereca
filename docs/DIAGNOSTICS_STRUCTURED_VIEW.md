# Diagnostics P0 #1012 — détails structurés et expurgés

La page Diagnostics ne présente plus les objets comme des mappings Python.
Les cartes événements, contrôles actifs, diagnostics runtime et système
proposent un panneau repliable, sélectionnable, avec défilement indépendant.

Trois vues sont issues du MÊME instantané expurgé :

- Lisible : hiérarchie indentée, listes et types explicites.
- JSON indenté : Unicode conservé et copie du texte courant.
- Brut expurgé : chaîne non interprétée, ou JSON compact des structures.
  Jamais le payload brut avant expurgation.

Horodatage, module, sévérité, provenance, statut et opération restent visibles.
Les panneaux sont repliés par défaut. Copier utilise le contenu visible
après expurgation, non l'objet source.

Sécurité : seconde frontière d'expurgation sur clés sensibles (secrets,
tokens, comptes, credentials, profils, ACSM, headers, chemins) et valeurs
(URL, courriels, chemins personnels et Bearer). Aucune valeur supprimée
par le Core n'est reconstruite. Les types null/false/0/chaîne vide/[]/{}
restent distincts et les chaînes ordinaires ne sont pas parsées en JSON.

Limites : 300 nœuds, 50 éléments/collection, 7 niveaux, 800 caractères
par chaîne et 24 Kio par rendu. Cycles et objets non pris en charge
produisent un marqueur explicite. Le mode JSON demeure valide même si
l'aperçu est tronqué.

Aucun nouvel appel réseau, accès filesystem, LLM ou workflow CI.
La recette visuelle et clavier revient au rôle UX (#1011), indépendant.
