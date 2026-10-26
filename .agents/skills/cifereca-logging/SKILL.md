---
name: cifereca-logging
description: Contrat impératif des logs pour Core et plugins, journal intégral, trace/Diagnostics unifiés, seul le login/mot de passe pseudonymisé en IHM.
---

# Journalisation Cifereca : skill commun Core / Plugins

**Obligatoire** pour tout travail sur événements, logs, providers, exceptions, Diagnostics, IHM, export, recherche, CLI `--trace` ou runtime de plugins.

Lire [le contrat canonique](../../../docs/LOGGING-CONTRACT.md) et [#2900](https://github.com/pzia/cifereca/issues/2900). **Il prévaut sur toute ancienne liste blanche ou consigne d'expurgation.**

## Confidentialité : limiter les travaux aux credentials

**Seuls les credentials d'authentification sont des secrets à protéger** : mot de passe, jeton d'accès/refresh, cookie de session authentifiant, clé API/privée et lien signé d'accès. Un identifiant de compte ou `credential_id` n'est pas un secret. Les erreurs, stack traces, messages, HTTP, chemins, noms, titres, emails, identifiants, journaux et captures *sans credential* n'exigent **aucune tâche de confidentialité spécifique** : ni expurgation, ni ticket, ni nouvelle recette, ni limitation du diagnostic.

La protection des credentials s'effectue par les permissions locales restrictives et l'absence de transfert/publication automatique non volontaire ; l'export demandé par l'utilisateur demeure **brut**, après confirmation. Ne pas créer de mécanisme de masquage général ; préserver la seule règle visuelle historique login/mot de passe dans Diagnostics.

## Règles à respecter

- Journaliser **toutes les données effectivement émises sans aucune expurgation** dans le Core, les plugins, le journal disque, le terminal, le broker, la recherche ou l'export **brut**.
- **Dans le rendu de l'IHM exclusivement**, pseudonymiser **la valeur login** et **la valeur mot de passe**, avec pseudonymes stables durant la session. **Tout le reste est visible** : comptes, identifiants, chemins, emails, URL, en-têtes, cookies, tokens, détails d'erreur et traces.
- La copie **du texte affiché** garde les pseudonymes ; un export **brut** ne les applique pas.
- Viser un **seul journal**, une **recherche globale**, et un **seul niveau réel** Core+Plugins où `--trace` et le réglage de l'IHM se reflètent dans les deux sens.
- Pour l'observabilité HTTP, fournir le statut/route/phase réels lorsque connus ; pas de suppositions ni de nouvelles captures réseau simplement pour enrichir un log.
- Aucun transfert/publication automatique de journal ou capture brute ; prévenir avant export, permissions locales restrictives, tests synthétiques seulement.

## Contrôle et frontières

Le [rôle core](../../roles/core.md) possède l'implémentation du logger, broker, recherche, CLI et IHM. Le [rôle plugins](../../roles/plugins.md) possède la production d'événements fournisseurs via `cifereca.plugin_api`. Les tests utilisent [cifereca-tests](../cifereca-tests/SKILL.md).

**Ne jamais présenter un changement documentaire comme une mise en conformité exécutée** : les filtres d'exécution et l'IHM historiques peuvent rester divergents.
