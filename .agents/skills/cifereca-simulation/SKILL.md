---
name: cifereca-simulation
description: Modifier ou utiliser le banc de simulation fonctionnelle Cifereca, actuellement sous gen-dataset, avec faux services déterministes, profils UX et isolation d'état.
---

# Simulation fonctionnelle Cifereca

Le banc actuel se trouve sous `gen-dataset/`. Malgré son nom historique, le traiter comme une simulation de l'écosystème métier, pas comme un simple générateur de datasets.

## Responsabilités

Le banc peut fournir :

- faux services Kobo, Numilog, Paris, BnF, OPDS et autres fournisseurs ;
- données et assets synthétiques ;
- profils fonctionnels reproductibles ;
- états d'erreur déterministes ;
- isolation XDG et configuration Cifereca ;
- orchestration démarrage/arrêt/health checks.

Il ne doit pas contenir les assertions métier principales de Cifereca.

## Concevoir un faux service

- Reproduire le contrat observable réellement nécessaire au consommateur.
- Garder les réponses déterministes via des seeds/versionnements explicites.
- Centraliser les données génériques dans les helpers communs ; conserver le protocole et les états métier dans le service concerné.
- Préférer des modes d'erreur configurables et reproductibles aux pannes aléatoires.
- Ne pas recopier inutilement la logique de production du plugin dans son simulateur.

Ajouter ou mettre à jour un self-test sous `gen-dataset/tests/` pour le contrat du faux service.

## Profils

Préférer un profil TOML versionné sous `gen-dataset/profiles/` lorsqu'un scénario doit être rejoué par plusieurs tests ou par une inspection UX.

Commandes usuelles :

```bash
python gen-dataset/datasetctl.py doctor <profil>
python gen-dataset/datasetctl.py up <profil>
python gen-dataset/datasetctl.py status <profil>
python gen-dataset/datasetctl.py summary <profil>
python gen-dataset/datasetctl.py down <profil>
```

Toujours arrêter le profil après un scénario automatisé, y compris en cas d'échec.

## Consommateurs

Un test de plugin utilisant un faux service reste sous le plugin. Un scénario transverse reste dans les tests centraux. Seuls les tests du banc lui-même vivent sous `gen-dataset/tests/`.

## Renommage futur

Si une tâche demande explicitement le renommage `gen-dataset/` vers `simulation/`, traiter ce changement comme une migration atomique : chemins de workflows CI, imports, documentation, variables, scripts et références doivent être mis à jour ensemble. Ne pas effectuer ce renommage opportunistement.
