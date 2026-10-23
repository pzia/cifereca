# Diagnostics — domaines métier attestés (P1 #2745)

## Contrat

**Module technique**, **fournisseur**, **domaine métier**, **sévérité** et
**période** sont des filtres indépendants. Le domaine est un identifiant métier
stable et fermé, pas une déduction du nom du logger, d'un champ `detail` libre,
d'un titre, d'une URL ou du texte d'un message.

Registre et résolution d'alias partagés par Core et plugins :
`cifereca.plugin_api.diagnostic_domains`. Les plugins **ne doivent jamais
importer `cifereca.core`** ; une famille n'est publiée dans la projection du
Core que lorsque le producteur exact, son schéma et sa provenance sont
validés. La classification ne transforme pas l'observation en preuve de droit
d'achat, de prêt ou de fichier.

| Code stable | UI | Preuve minimale de la classification |
|---|---|---|
| `browser_recorder` | Browser Recorder | Résultat contrôlé du Browser Recorder attesté par Core |
| `reconciliation` | Réconciliation | Audit terminal local attesté, schéma et statut cohérents |
| `provider_connection` | Connexions fournisseur | Résultat Core de connexion ou trace de connexion strictement reconnue |
| `source_sync` | Synchronisations | Résultat Core/source sync strictement reconnu |
| `loan_download` | Téléchargements de prêts | Résultat terminal de téléchargement de prêt, ou trace dédiée reconnue |
| `kobo_backup` | Sauvegardes Kobo | Décision Kobo de sauvegarde attestée ou trace Core stricte |
| `numilog` | Numilog | Démarrage attesté ou trace de session Numilog strictement reconnue |
| `adept` | ADEPT | Résultat runtime ADEPT attesté ou trace Core stricte |

Les alias reconnus (`Browser`, `recorder`, `Réconciliation`,
`Reconciliation`, `Réconcilitation`, etc.) sont résolus en codes stables.
Les noms de compte, intitulés arbitraires et valeurs non connues sont refusés.

## Parcours de consultation

Dans **Diagnostics → Journaux**, choisir **Domaine métier**.
Le Core applique cette sélection **avant calcul du total et pagination**.
Le filtre de module conserve son rôle technique ; le champ de recherche texte
et le tri ne s'appliquent qu'à la page affichée. « Tous les domaines »
affiche aussi les événements génériques non classifiés ; un domaine sélectionné
n'inclut jamais ces événements uniquement parce que leurs messages se
ressemblent.

La sélection/export multi-page et la prévisualisation IA doivent conserver le
même code de domaine, vérifier la portée des événements renvoyés et refuser
un Core incompatible plutôt que simuler un filtrage local exhaustif.
La prévisualisation IA ne contacte aucun modèle avant consentement explicite.

## Ajouter une nouvelle famille

1. Définir un code canonique non privé dans le registre public et un libellé UI.
2. Décrire le producteur Core (ou schéma de trace **strictement reconnue**) et
   les opérations/statuts exacts : aucun choix basé sur le texte libre ni sur
   une catégorie `PLUGIN` générique.
3. Ajouter la classification dans `diagnostic_event_feed.py` **après** la
   validation du producteur, sans modifier le logger ni inventer de fournisseur.
4. Tester les deux canaux si disponibles, les fausses observations, les alias,
   la pagination après 100 événements de bruit, l'export, le consentement IA,
   les sources partielles et l'absence de chemin/compte/titre/URL privée.
5. Valider CI Core/Plugins, installation release + rollback et GTK.

## Limites honnêtes

Le journal applicatif est tronqué/borné et le collecteur Core est en mémoire ;
une page filtrée vide ne prouve pas qu'une opération n'a pas eu lieu.
Un domaine publié atteste seulement la **nature** de l'événement observable,
pas l'état de santé du fournisseur ni une corrélation de tâche non publiée.
