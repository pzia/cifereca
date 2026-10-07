# Simulation fonctionnelle CIFERECA

État documentaire : 7 octobre 2026.

## État actuel

Le dépôt possède `gen-dataset/`.

Il contient notamment :

- des faux services BnF, Kobo, Paris, Numilog, OPDS/Ebooks gratuits ;
- des fixtures/livres déterministes ;
- des profils TOML ;
- `datasetctl.py` pour l'orchestration, l'isolation XDG et les health checks ;
- des tests propres au banc de simulation.

Le dépôt ne possède pas encore de répertoire `simulation/`.

## Cible

`simulation/` doit devenir l'environnement fonctionnel simulé de référence.

La cible n'est pas de copier `gen-dataset/` puis de maintenir deux systèmes. La migration doit aboutir à une seule référence :

```text
gen-dataset/  -- migration progressive -->  simulation/
                                      \
                                       -> suppression des éléments obsolètes
```

## Responsabilités de la simulation

La simulation fonctionnelle peut porter :

- faux services versionnés ;
- états fournisseur reproductibles ;
- jeux de données et fixtures ;
- profils/scénarios ;
- orchestration de services ;
- génération de configuration isolée ;
- collecte de résultats fonctionnels ;
- scénarios reproductibles d'erreur/latence/volume.

Elle ne doit pas porter :

- bootstrap GTK ou dépendances OS ;
- logique applicative Cifereca ;
- tests unitaires Core ;
- règles fournisseur réelles qui doivent vivre dans les plugins.

## Relation avec `environnements/`

`environnements/` prépare la machine/runtime. La simulation prépare le monde fonctionnel autour de Cifereca.

Exemple d'un E2E :

1. `environnements/openai-chat-debian/` fournit GTK/Xvfb/D-Bus ;
2. la simulation démarre les faux services et construit le profil ;
3. Cifereca est lancé contre cet état isolé ;
4. le scénario vérifie le comportement fonctionnel/UX.

## Plan de migration de `gen-dataset/`

Aucun déplacement n'est effectué par le lot documentaire.

Migration recommandée :

1. inventorier les faux services et helpers encore utilisés ;
2. définir l'interface de la future orchestration `simulation/` ;
3. migrer les briques génériques (`fake_books`, profils, health checks) sans duplication ;
4. migrer les protocoles fournisseur avec leurs tests ;
5. mettre à jour les recettes E2E et les skills ;
6. retirer les chemins `gen-dataset/` devenus inutilisés ;
7. supprimer `gen-dataset/` lorsque plus aucun consommateur ne dépend de lui.

## Règle pendant la transition

Tant que `simulation/` n'existe pas, `gen-dataset/` reste la référence fonctionnelle pratique.

Ne pas créer un nouveau faux service dans une arborescence concurrente sans lot de migration explicite.
