# Handoff — Mutualisation Core / Plugins

État au 7 octobre 2026.

## Statut

Le chantier transversal de mutualisation Core / Plugins est **clos**.

- Repository : `pzia/cifereca`
- Branche de référence : `main`
- État fonctionnel : aucun blocage restant sur le plan initial
- PR ouvertes au moment de la clôture : aucune
- CI `Test plugins` et `Package plugins` : verte sur les merges de clôture

## Mutualisations livrées

### Exécution et UI

- `TaskBroker` public pour les traitements de fond, progression et annulation.
- Présentation maître/détail standardisée côté Core et utilisée par le plugin Recommandations.
- L’entrypoint actif Recommandations utilise `TaskBroker` et la présentation maître/détail publique.

### Transport et artefacts

- Plugin transversal `transport.http` pour les téléchargements HTTP/HTTPS publics bornés.
- Inspection publique d’artefacts PDF / EPUB / ACSM, y compris en mémoire.
- Détection ACSM fichier mutualisée avec Numilog.
- Publication atomique de contenu en mémoire via `publish_artifact_content(...)`.
- Publication atomique de fichiers déjà streamés via `publish_artifact_file(...)`.
- `transport.http` et Numilog utilisent la primitive publique de publication finale.

### Cache et diagnostics

- `CacheBroker` public :
  - `get`, `put`, `delete` avec TTL ;
  - namespace automatiquement scopé par plugin ;
  - single-flight via `get_or_compute(...)`.
- Contrat de health-check / diagnostics public.
- `redact_text(...)` exposé via l’API publique ; Kobo Vault n’embarque plus sa copie locale.

### Normalisation

- Normalisation de langue mutualisée.
- Sanitisation de noms de fichiers mutualisée.
- Normalisation HTML/XHTML des descriptions exposée depuis le Core.
- Suppression des clones de description dans :
  - Paris ;
  - Gutenberg ;
  - Gallica ;
  - Kobo ;
  - BnF Catalogue.

## Principales PR du chantier

- #46 — TaskBroker public.
- #50 — transport HTTP transversal.
- #54 — nettoyage validation artefacts.
- #57 — Baen vers transport HTTP.
- #59 — Ebooks libres et gratuits vers transport HTTP.
- #67 — inspection d’artefacts en mémoire.
- #69 — publication atomique de contenu.
- #70 — inspection ACSM fichier / Numilog.
- #73 — CacheBroker clé/valeur avec TTL.
- #75 — CacheBroker single-flight.
- #78 — sanitisation publique des noms de fichiers.
- #80 — normalisation publique des descriptions HTML/XHTML.
- #84 — redaction diagnostics publique.
- #86 — publication commune de fichiers déjà streamés.
- #88 — finalisation de la publication Numilog via la primitive commune.

## Dettes volontairement laissées hors périmètre

### Recommendations legacy

L’ancienne hiérarchie contient encore une implémentation `threading.Thread` dans la classe de base historique. L’entrypoint actif v0.2.4 surcharge le parcours concerné via les classes v0.2.2+ et utilise `TaskBroker`.

Ce point est une dette de structure, pas un défaut runtime. Un nettoyage futur demanderait une refactorisation de hiérarchie, pas une simple suppression de code mort.

### Validation EPUB stricte Numilog

Le validateur EPUB Numilog reste volontairement local car il est plus strict que l’inspection Core actuelle. Il vérifie notamment :

- le nombre maximal d’entrées ZIP ;
- les doublons de noms ;
- les entrées ZIP chiffrées ;
- la structure OPF ;
- certaines protections Adobe / LCP.

Ne pas supprimer ce validateur tant que l’équivalence n’est pas démontrée dans le Core.

### Sessions HTTP authentifiées

Paris, Numilog, Kobo et certains catalogues conservent leurs sessions HTTP spécialisées. Ces flux portent authentification, cookies, navigation ou règles métier et ne doivent pas être forcés dans `transport.http` sans extension explicite de son contrat public.

## Règle pour les évolutions futures

Avant d’ajouter une primitive dans un plugin :

1. vérifier si un broker, helper ou contrat public Core existe déjà ;
2. si plusieurs plugins portent la même logique, préférer une primitive Core minimale et testée ;
3. conserver les règles métier et protocolaires spécifiques dans le plugin ;
4. ajouter au moins un test de contrat plugin pour toute nouvelle primitive Core destinée aux plugins.

## Conclusion

Le plan initial de mutualisation est couvert. Les dettes restantes sont identifiées et volontairement différées ; aucune n’est bloquante pour le fonctionnement actuel.
