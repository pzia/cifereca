# #2380 — Matrice transverse de diffusion des diagnostics (contrat v1)

**Propriétaire : Core.** Cette matrice couvre les schémas **effectivement
implémentés** au 20 octobre 2026 ; elle n'accorde aucune nouvelle permission
d'accès, de journalisation, d'export ou d'analyse distante. Les schémas
particuliers, liés ci-dessous, gardent l'autorité sur les énumérations, bornes
et tests. Un nom de champ n'est **jamais** une preuve de provenance.

## Canaux et classe de publication

| Classe | GUI Diagnostics locale | `diagnostics.events` | Copie/export support volontaire | Journal local de l'émission Core | Artefacts CI / IA distante |
| --- | --- | --- | --- | --- | --- |
| Terme technique fermé, **validé par le producteur Core** (opération, phase, statut, code, booléen de capacité) | Autorisé, si publié | Autorisé | Projection publique seule | Trace Core canonique ou code fixe | Pas d'envoi automatique ; même projection après consentement et contrôle de l'artefact |
| `provider_id` publié par le **registre Core** (slug fermé / fallback `source_inconnue`) | Si attesté | Si attesté | Si attesté | Si attesté | Même règle de consentement |
| `duration_ms`, HTTP status et compteurs non négatifs, bornés, réellement mesurés | Si attestés | Si attestés | Si attestés | Si attestés | Idem ; aucune conversion d'un texte libre en nombre |
| `task_ref` opaque, aléatoire, fourni par le contexte du worker Core | Si émis | Si émis | Seulement si explicitement sélectionné et conservé après expurgation | Si émis | Pas de propagation implicite à un tiers ; limiter la réidentification |
| Identité, `credential_id`, compte, profil ADEPT, numéro de prêt, `source_item_id`, titre, empreinte de fichier, alias utilisateur | Interdit dans le flux général | Interdit | Interdit | Interdit dans les émissions structurées partagées | Interdit |
| Exception libre, traceback, stdout/stderr, HTML/XML/ACSM, contenu EPUB, URL de livraison ou de suivi, token, cookie, chemin personnel, clé ADEPT | Interdit | Interdit | Interdit | Jamais copié dans une émission structurée ; le log historique brut reste hors API | Interdit |

**Séparer impérativement** lecture du log complet local (hors broker) et
publication de la projection expurgée. Activer DEBUG ne modifie **pas** cette
matrice. Une vraie référence peut aider à regrouper deux observations ; elle
ne démontre pas une cause ni un succès métier. `event_id` est un identifiant
de ligne/snapshot local, pas un identifiant de tâche global. Une proximité
temporelle n'est jamais une corrélation.

## Projections attestées et couverture

| Producteur / événement | Provenance contrôlée | Contrat lisible dans les deux canaux | Limites non couvertes |
| --- | --- | --- | --- |
| Décision `KOBO / automatic backup decision` | Producteur Core et schéma Kobo réservé | `kobo_backup/decision`, `status`, activation, comptes connectés/éligibles, intervalle | Pas de `task_ref` partagé ; aucun droit ou fichier déduit |
| `NUMILOG / startup_materialization` | Émission Core validée avant log/collecteur | Phases, causes, compteurs, références quand réellement émises | Résultat de worker ≠ résultat d'acquisition ; pas de vérité sur une livraison non observée |
| `source_sync` start/terminal | Producteur Core `synchronize_account_source`, registre fournisseur et contexte worker | `provider_id` ou `source_inconnue`, phase/statut/code fermés, compteurs/durée si attestés, `task_ref` si présent | Pas de code HTTP/fournisseur ni garantie d'achats téléchargés |
| `kobo_adept` terminal | Plugin **et package runtime** vérifiés par Core ; émetteur réservé | Opération ADEPT qualifiée, phase terminale, code connu ou `code_non_disponible`, durée/code retour si observés, référence worker éventuelle | Pas de causes Adobe inventées, callback brut, contenu de processus ni association fournisseur déduite |
| Échec connexion `PLUGIN / provider connect` | ID vérifié du registre Core ; échec validé | Slug public qualifié et code fermé, dans les deux canaux (#2380) | L'alias configuré n'est pas public ; generic `Diagnostics.add` de plugin n'atteste rien |
| Autres diagnostics/TRACE historiques | Aucune preuve de schéma commun | Vocabulaire fixe limité ou message masqué | Pas de promesse de symétrie des deux sources ni de task_ref |

Références de schéma : [Kobo](CORE-P0-2380-KOBO-DECISION-CHANNELS.md),
[Numilog](CORE-P0-2380-NUMILOG-SHARED-EMISSION.md),
[source-sync](CORE-P0-2380-SOURCE-SYNC-EVENTS.md),
[ADEPT](CORE-P0-2380-ADEPT-EVENTS.md),
[connexions](CORE-P1-2370-CONNECTION-DIAGNOSTICS.md) et
[deux canaux de connexion](CORE-P0-2380-CONNECTION-CHANNELS.md).

## Couverture, rétention et tiers

- Collecteur structuré : fenêtre mémoire bornée à 500 événements au plus,
  susceptible d'être filtrée ou désactivée. Aucune relecture historique
  garantie.
- Journal : **quatre** fichiers au maximum (actif + trois rotations), **32
  Kio** de queue et **200 lignes** par fichier ; permissions, rotation et
  activation des niveaux peuvent modifier la visibilité. `partial`,
  `unavailable` et `disabled` ne doivent pas être cachés par l'autre canal.
- Horodatages : publication UTC dans le flux après #2523 ; ancienne ligne de
  log sans offset interprétée dans le fuseau local **du lecteur**, qui peut
  avoir changé depuis l'écriture. L'interface peut présenter en heure locale
  mais doit rendre le fuseau intelligible. Les intervalles ne prouvent pas
  une causalité.
- Export, presse-papiers, CI, IA : réutiliser **la même projection fermée** ;
  aucun enrichissement via des fichiers locaux, exceptions, secrets ou
  identifiants pour compenser une preuve absente. L'analyse et l'upload
  restent explicitement déclenchés par l'utilisateur. Sur CI, n'utiliser que
  des fixtures synthétiques expurgées.
- Risque résiduel : la simple présence d'un plugin et la répétition d'un
  `task_ref` peuvent être révélatrices d'usage ; contrôler le périmètre
  d'une sélection et son destinataire. Un plugin Python dans le processus
  n'est pas un périmètre d'isolation contre du code arbitrairement malveillant.

## Recette de non-régression

Tester séparément **l'émission commune**, la validation de chaque canal,
le masquage des champs hostiles et les consommateurs (UI, copie, export,
IA sur consentement). Fixtures : sauvegarde Kobo `partial` avec
`source_sync success` indépendant ; deux connexions en erreur sans
compte exposé ; runtime ADEPT connu/inconnu ; Numilog succès/échec et
références worker valides, absentes ou contrefaites. Comparer **uniquement**
les champs typés effectivement publiés des deux observations, sans fabriquer
une entrée absente d'un canal. Un test synthétique ne vaut pas recette terrain
Kobo, Adobe, Paris ou Numilog.

Cette matrice n'implémente pas les producteurs restants, l'exhaustivité
historique ou la recette de bout en bout ; **#2380 reste ouvert**.
