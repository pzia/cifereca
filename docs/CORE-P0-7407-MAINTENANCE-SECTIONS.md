# Maintenance : matrice de présentation (#7407)

Les groupes sont des capacités de présentation additives `maintenance.group.*`, sans autorité métier ni nouveau registre. Les contributions anciennes, ambiguës ou tierces non classées sont visibles dans une section de compatibilité. L'hôte existant conserve admission, preview, confirmation, annulation et révocation. Aucun outil suspendu n'est activé au montage.

| Contribution | Groupe principal | Contrat / ticket | Disponibilité livrée |
|---|---|---|---|
| maintenance.library.organizer | Organisation | library.organizer.read / #3176, #7087 | Revue, plan à blanc, aucune mutation |
| maintenance.audit.naming | Organisation | metadata, library.naming / #3995 | Suspendu par garde manager |
| maintenance.repair.naming | Organisation | library.maintenance / #3185 | Suspendu par garde manager |
| maintenance.cleanup.duplicates | Organisation | library.maintenance / #3185, #4496 | Gel destructif |
| maintenance.audit.integrity | Intégrité | metadata, library.read / #3185 | Suspendu par garde manager |
| maintenance.audit.covers | Qualité | catalog / #3995 | Audit read-only, version réparée admise |
| maintenance.audit.languages | Qualité | catalog / #3995 | Audit read-only, version réparée admise |
| maintenance.audit.metadata | Qualité | catalog / #7407, #6366 | Rapport agrégé partiel des divergences et compléments C ; aucune correction |
| maintenance.library.local-origin | Provenance | library.origin.read, library.origin.detail.read / #4030 | Comparaison documentaire bornée |
| Adaptateur ADEPT historique — provenances | Provenance | Core / #976 | Diagnostic et association sous gardes existantes |
| Adaptateur ADEPT historique — verrous | Reprise | Même contrôleur Core / #7244 | Diagnostic des verrous et vérification des fichiers ; aucun déverrouillage autorisé |
| Téléchargements à reprendre (adaptateur CoreUI) | Reprise | Même dialogue/file que Synchronisation / #7161, #7231 | Réarmement individuel admissible, aucun GET au clic |
| maintenance.cleanup.duplicates.recover | Reprise | library.maintenance / #3185 | Reprise supervisée des tombstones |
| maintenance.library.derived-variants | Reprise | library.derived.read / #6937 | Revue et simulation ; pas de reprise effective |
| maintenance.library.derived-capacity | Stockage | library.derived.read / #6937 | Observations séparées ; aucune restitution de quota |
| core.maintenance.cache | Stockage | cache / #3996 | Suspendu par garde manager |

Limites métier : suggestions détaillées par livre (rapport agrégé livré, aucune correction), reprise effective des variantes et organisation mutatrice non autorisées par cette réorganisation. Recette GTK intégrée à valider. Le classement des contributions n'est pas une réception de leur moteur métier.

## Migration des anciens accès

- Les anciennes commandes de nommage, renormalisation et doublons restent identifiées comme suspendues dans Organisation ; le parcours guidé est l'entrée de revue. Aucune des anciennes commandes n'est réactivée.
- Le renvoi déclaratif Organisation → Reprise ne lance pas un plan : il déplace seulement le focus sur le groupe des opérations interrompues, dont les tombstones existants. Le contrôle et la confirmation restent dans l'unique commande propriétaire.
- Synchronisation et Reprise utilisent le même dialogue de téléchargements ; les verrous ADEPT ambiguës restent non réarmables.
- Les états indisponibles distinguent garde de sécurité, port absent, contrat incompatible, configuration/dépendance absente et désactivation, sans exposer le diagnostic privé.
