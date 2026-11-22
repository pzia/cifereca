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
| maintenance.library.local-origin | Provenance | library.origin.read, library.origin.detail.read / #4030 | Comparaison documentaire bornée |
| Adaptateur ADEPT historique | Provenance | Core / #976, #7244 | Diagnostic et association sous gardes existantes |
| maintenance.cleanup.duplicates.recover | Reprise | library.maintenance / #3185 | Reprise supervisée des tombstones |
| maintenance.library.derived-variants | Reprise | library.derived.read / #6937 | Revue, capacité et simulation ; pas de reprise effective |
| core.maintenance.cache | Stockage | cache / #3996 | Suspendu par garde manager |

Restent à livrer : dissociation capacité/reprise dans le package variantes, accès contextuel unique aux téléchargements suspendus, intégration des métadonnées contradictoires à la qualité documentaire, renvois des actions historiques et recette GTK sur le parcours complet. Le classement des contributions n'est pas une réception de leur moteur métier.
