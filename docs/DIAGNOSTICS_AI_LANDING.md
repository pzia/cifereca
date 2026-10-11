# Diagnostics et IA — périmètre d’atterrissage P0 (11 octobre 2026)

## Livrable vérifiable dans le code

La page Diagnostics conserve **deux traitements distincts** :
- consultation, filtrage et copie d'événements expurgés via le broker Core public (déterministe, sans modèle) ;
- analyse facultative par le service LLM existant, **uniquement après** prévisualisation locale et clic « Autoriser et analyser ».

L'aperçu IA utilise désormais exactement les filtres affichés **source, module, sévérité et période**. Une modification de filtre invalide l'aperçu et toute autorisation associée. Une requête lit au plus **24 événements** du broker et son texte envoyé est borné à **3 000 octets UTF-8**. Aucune consultation des fichiers privés par le plugin ; pas de réseau ni de génération IA à l'ouverture de la page.

Le nom du fournisseur/modèle affiché dans le résultat est la **destination déclarée avant consentement**, pas une attestation du backend. La preuve rapportée reste limitée aux identifiants de l'échantillon présents dans la réponse : une citation ne prouve pas la véracité de l'interprétation. Les refus de planification ou sorties invalides ne doivent pas laisser une ancienne analyse utilisable comme résultat courant.

## Recette ciblée (à effectuer avant clôture)

1. **Sans LLM** : ouvrir Diagnostics, rafraîchir les journaux ; vérifier zéro requête au modèle et interface lisible.
2. **Prévisualiser** : choisir source, module, sévérité, période ; vérifier les filtres transmis au broker, la destination local/distant, le nombre d'événements et les octets annoncés. Aucun appel `generate` avant consentement.
3. **Modifier un filtre** entre aperçu et accord : boutons d'envoi désactivés, aperçu périmé invalidé ; nouvel aperçu et nouveau consentement nécessaires.
4. **Autoriser** une fois : un seul appel au LLM ; second essai avec la même autorisation refusé, changement de destination refusé.
5. **Échecs** : broker absent, collection vide, tâche refusée, sortie LLM vide, modèle indisponible, annulation, source partielle, faux noms de modèle/fournisseur dans la réponse ; aucune ancienne analyse présentée comme fraîche.
6. **Confidentialité** : emails, chemins privés, jetons, URL signées, texte hostile dans les logs ; inspecter l'aperçu **avant** envoi et le rapport/copier. Un message hostile reste une donnée, jamais une commande.
7. **GTK réel / rôle UX #1011** : clavier et focus, défilement des longs rapports, copier, changements rapides de filtres et tâches asynchrones ; vérifier le responsive aux résolutions de référence.

Tests unitaires existants : `src/plugins/diagnostics-page/tests/test_diagnostics_explorer_p0.py` et `test_diagnostics_ai_p0.py`. Commande ciblée (environnement Python du dépôt) :

```bash
PYTHONPATH=src:src/plugins/diagnostics-page python -m pytest -q \
  src/plugins/diagnostics-page/tests/test_diagnostics_explorer_p0.py \
  src/plugins/diagnostics-page/tests/test_diagnostics_ai_p0.py
```

Ne pas déclencher de nouvelle GitHub Action pour cette recette : validation locale ou runner déjà disponible, puis recette GTK sous rôle UX.

## Reste explicitement **non livré** (ne pas clore #1009 comme exhaustif)

- Analyse multi-source combinant événements, snapshot, checks, rapport support et fichiers **explicitement sélectionnés**. Le MVP IA examine uniquement `diagnostics.events()`, dont les sources sont celles exposées par le broker.
- Traçage déterministe de chaînes d'opérations et référence de lignes/plages dans des fichiers externes ; aucune corrélation causale « inventée ».
- Export IA structuré en Markdown **et** JSON avec toutes les preuves ; la page permet actuellement de copier le compte rendu textuel.
- Recette complète avec vrais journaux anonymisés 10/10/2026, exécutions GTK réelles et comportements timeout/transports LLM configurés.
- Évolutions Core #1010, instrumentation Jeunesse #907 et UX #1011 suivies séparément.

## Critères de passage au Merger

Fusionner **une seule PR de consolidation IA** après revue des conflits et vérification pytest ciblée ; éviter des fusions indépendantes des PR historiques qui réécrivent les mêmes fonctions. Conserver #1009 ouvert pour les fonctionnalités non livrées, et réserver la recette GTK à l'agent UX (hors CI). Le rôle Focus ne fusionne pas les PR.
