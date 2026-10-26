# Contrat canonique de journalisation — Core, Plugins et IHM

**Décision impérative du propriétaire, 25 octobre 2026 — [#2900](https://github.com/pzia/cifereca/issues/2900).** Ce document prévaut sur les anciens contrats de redaction, listes blanches, suppression de contexte, codes masqués et exports expurgés. Il constitue **la cible** : son existence ne prouve pas que l'implémentation actuelle soit conforme.

## Règle obligatoire

1. **Tout journaliser, sans modifier les données.** Core, plugins et runtimes conservent les événements et détails **tels qu'effectivement émis**, sans suppression, expurgation, anonymisation ou pseudonymisation à l'émission, sur disque, en console, dans le broker Diagnostics, dans la recherche et dans les exports **bruts**. Les champs libres et détails de cause sont conservés, même sensibles : chemins, URL complètes, query strings, entêtes, cookies, tokens, autres identifiants, comptes, profils, emails, titres, corps et traces d'erreur lorsqu'ils sont émis. Aucune liste blanche de publication ne peut remplacer le message ou retirer un champ. Cette règle n'ordonne **aucune nouvelle capture HTTP, collecte d'authentification, requête réseau ou mutation métier**.
2. **Masquage à l'IHM, et à l'IHM seulement.** Le **rendu visible de Diagnostics** remplace exclusivement les **valeurs de login / identifiant de connexion** et les **valeurs de mot de passe** par des pseudonymes simples et stables pendant au moins une session, par exemple `login-1`, `login-2`, `mot-de-passe-1`. Les variantes `username` et `passwd` sont visées **seulement** si elles représentent effectivement ces deux champs. Aucun autre champ n'est caché, même s'il semble confidentiel. En particulier `credential_id`, `account_id`, `profile`, `loan_id`, `email`, URL, cookies, tokens, headers, chemins et erreurs restent visibles.
3. **Copie ≠ export brut.** La copie du **texte affiché** reproduit les pseudonymes vus à l'écran. L'**export brut** conserve strictement les valeurs d'origine, y compris le login et le mot de passe s'ils étaient présents dans la source. Les filtres/recherches s'appliquent aux valeurs originelles, sans altération du stockage ni du broker.
4. **Traçabilité technique.** Publier et rendre recherchables les champs disponibles : timestamp, niveau, producteur, provider, site, opération, étape précise, `task_ref`, statut HTTP effectivement reçu, URL/route réelle, message d'erreur et sa cause. Aucun `[opération masquée]`, `contenu libre masqué` ni substitution par un code générique ne doit effacer la preuve. Ne jamais **inventer** une étape, une tentative HTTP, un statut ou une causalité.
5. **Un seul journal comme cible.** Core et plugins alimentent un journal d'événements unique. Les doubles projections historiques `core_events` / `app_log` ne doivent pas devenir deux tentatives ; utiliser leurs clés de corrélation réelles. L'IHM propose **un champ de recherche globale** couvrant toutes les lignes et pages accessibles. CLI `--trace` et réglage IHM commandent **un seul niveau effectif**, synchronisé dans les deux sens. Le niveau de détail `normal / verbose / trace` est indépendant du masquage.
6. **Responsabilités.** Le Core possède le stockage, le broker, le niveau effectif, la recherche et le rendu IHM. Les plugins fournissent leurs messages métier et techniques par les API publiques, sans pré-masquer. Aucune dépendance aux modules privés du Core pour contourner cette frontière.

## Périmètre de confidentialité : credentials uniquement (26/10/2026)

La **confidentialité ne justifie des protections spécifiques que pour les credentials** : mots de passe, jetons bearer/refresh, cookies de session authentifiants, secrets API, clés privées et liens signés donnant accès. Les identifiants techniques (`credential_id`, `account_id`, `loan_id`), erreurs, URL ordinaires, traces, titres, chemins, noms, emails, métadonnées et captures sans secret sont **librement conservés et exploitables pour le diagnostic**. Leur présence, même dans un journal, une capture ou un artefact, **n'impose aucun travail de confidentialité additionnel** : pas d'expurgation, d'anonymisation, de filtre, d'audit ou de ticket par principe.

Ce cadrage ne modifie **pas** la fidélité du journal brut, de la recherche, de la console et de l'export utilisateur ni la pseudonymisation de **login et mot de passe dans l'IHM seulement**. La protection des credentials porte sur les permissions des fichiers et la prévention d'une diffusion automatique non voulue des secrets ; un export volontaire conserve les octets originels avec avertissement. Les prescriptions historiques plus larges de « non-publication de données privées » ne sont pas un motif pour étendre le périmètre d'une PR. Les autres gardes de sûreté, de droits et d'intégrité restent indépendants.

## Risque assumé et précautions de transfert

**Les journaux, consoles et exports bruts peuvent contenir des secrets et informations personnelles.** Restreindre les permissions locales ; avertir explicitement avant export ou partage. **Aucun upload/transfert automatique**, aucune publication de données privées dans GitHub, la CI, les artefacts, ou les tickets sans une action volontaire distincte. Ne pas committer des captures ou logs d'un compte réel. Les tests utilisent uniquement des données synthétiques.

## Contrôles contractuels

- Avec une trace de test `login=alice password=secret-test account_id=42 url=https://exemple.test/a?x=1 token=abc status_http=404`, le stockage, la console, le broker, l'export **brut** et le moteur de recherche conservent **tous les octets originaux**. **Au seul rendu de l'IHM** et lors de la copie de ce rendu : `alice` et `secret-test` sont pseudonymisés, tous les autres champs inchangés.
- Les messages/stack libres, URLs et codes HTTP ne disparaissent jamais par une liste blanche ou une catégorie inconnue. Un événement physique n'est pas compté deux fois lorsqu'il a deux représentations héritées.
- `--trace` se reflète dans l'IHM et inversement ; le même niveau s'applique aux producers Core **et** plugins, et la recherche porte sur le journal complet accessible.
- Les contrôles portent sur des fixtures synthétiques. Une validation documentaire ne remplace pas l'implémentation ni les tests en situation réelle.

## Application par les agents

[AGENTS.md](../AGENTS.md) ; [Core](../.agents/roles/core.md) ; [Plugins](../.agents/roles/plugins.md) ; [skill commun](../.agents/skills/cifereca-logging/SKILL.md) ; [frontières](PLUGIN-BOUNDARIES.md).

Les anciennes descriptions de [DIAGNOSTICS_EVENT_FEED.md](DIAGNOSTICS_EVENT_FEED.md) et de [DIAGNOSTICS_EXPLORER.md](DIAGNOSTICS_EXPLORER.md) documentent le fonctionnement hérité : **elles ne dérogent pas à cette consigne**.

**Hors périmètre du changement documentaire :** remplacer les filtres Python en production, migrer le stockage vers un journal physique unique, réécrire la CLI/l'IHM et les exports. Ces corrections nécessitent des PR/test dédiés (dont [#2900](https://github.com/pzia/cifereca/issues/2900)).
