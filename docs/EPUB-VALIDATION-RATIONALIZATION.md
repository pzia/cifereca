# Matrice de validation EPUB — Core / Numilog

État au 7 octobre 2026.

| Contrôle | Core strict | Numilog après rationalisation | Décision |
|---|---|---|---|
| ZIP lisible / CRC | oui | délégué au Core | Core générique |
| nombre maximal d’entrées | oui, 20 000 | délégué au Core | Core générique |
| noms d’entrées dupliqués | rejet | délégué au Core | Core générique |
| entrée ZIP chiffrée | rejet | délégué au Core | Core générique |
| `mimetype` présent/exact/borné | oui | délégué au Core | Core générique |
| `META-INF/container.xml` présent/XML valide/borné | oui | délégué au Core | Core générique |
| au moins un rootfile | oui | délégué au Core | Core générique |
| chemin OPF absolu ou avec `..` | rejet | délégué au Core | Core générique |
| OPF présent, XML valide, racine `package`, taille bornée | oui | délégué au Core | Core générique |
| `META-INF/license.lcpl` / `rights.xml` | signal de protection hors validation structurelle | rejet explicite | politique provider Numilog |
| `encryption.xml` font obfuscation standard | accepté | accepté | politique provider Numilog |
| autre chiffrement EPUB | Core expose `protected` quand détectable | rejet explicite | politique provider Numilog |

## Décision

Le validateur strict Numilog ne doit plus dupliquer les contrôles structurels et de sûreté génériques. Ceux-ci appartiennent à `cifereca.artifact_inspection` avec `strict_structure=True`.

Numilog conserve uniquement les règles liées à sa capacité fonctionnelle : refus LCP/Adobe et refus des méthodes de chiffrement autres que l’obfuscation de polices autorisée.

La suppression complète de `validate_epub()` dans le plugin n’est donc **pas** retenue : une fine couche provider reste nécessaire tant que le provider ne déclare pas la prise en charge de ces protections.
