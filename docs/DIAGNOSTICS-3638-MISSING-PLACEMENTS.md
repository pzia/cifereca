# Diagnostics des Placements manquants — #3638

## 29 octobre — réception du contrat Core #3645 par Diagnostics C

Le Core a fusionné le handoff [#3645](https://github.com/pzia/cifereca/pull/3645).
La projection Diagnostics accepte désormais **l'enveloppe privée attestée**
`cifereca.placement.observation/1` dans l'événement terminal existant,
sans nouveau scan, mutation, téléchargement, ni publication côté broker public.

Seuls les faits explicitement fournis par Core sont conservés : profil, IDs
SQLite, état du probe, motif observé, ancien état SQLite, chemin avec seules
valeurs de credentials protégées, couverture et borne. Aucun droit/restauration
n'est déduit du SHA, des dossiers, d'un transfert ponctuel ou d'un identifiant.
Le validateur rejette une enveloppe de portée invalidée ou malformée, une
raison/état contradictoires, une identité contenant un credential et une
autorisation de réparation/acquisition. Les anciens audits sans enveloppe
restent lisibles comme agrégats.

**Borne de sortie :** le Core remet au plus 256 lignes; C borne en plus le
détail JSON à 8 500 caractères ASCII échappés pour préserver le journal
physique (limite 16 KiB). `total_rows` garde la taille attestée du profil;
`emitted_rows` indique exactement le sous-ensemble publié;
`truncated=true` documente toute omission. Une ligne omise ou non probée
ne devient jamais `missing`. La même copie validée traverse la mémoire,
le journal canonique, la recherche, les détails et l'export brut; le retour
public du broker Core ne reçoit aucun champ privé.

Régression : `tests/test_diagnostics_p1_3638_placement_handoff.py` et
`tests/test_core_placement_observation_handoff.py`. Recette locale :
```sh
PYTHONPATH=src python -m pytest -q \
  tests/test_diagnostics_p1_3638_placement_handoff.py \
  tests/test_core_placement_observation_handoff.py \
  tests/test_diagnostics_missing_placements.py
```

La réception C peut être clôturée après preuves CI et intégration; les
réparations effectives #2847/#3185 et leur recette sur installation isolée
restent exclusivement à A.

---

Parent #2847 ; contrat directeur #3171 ; mutations #3185 ; recette isolée #3607.
Audit de `main` a897b446. Ce lot observe les résultats existants : aucun nouveau
scan, accès fournisseur, moteur de réparation, déplacement ou suppression.

## Surfaces et pertes constatées

| Surface existante | Faits disponibles | Limite / correction C |
| --- | --- | --- |
| `library_stored_paths.inspect_stored_paths` | État par index de ligne, double probe no-follow sous racine rouverte, compteurs et contexte SQLite | Core A exclusivement ; C réutilise le résultat, sans probe supplémentaire |
| `LibraryAudit.redacted()` | Statut, raisons, compteurs de chemins, ventilation SQLite, cardinalités Placement/Artifact, couverture des phases | Aucun profil, chemin ou ID par ligne n'est transmis |
| `library_audit_broker._run_scoped_audit` | Un résumé terminal après contrôle de portée | Ne transmet que le résumé ; le résultat privé CoreUI ne passe pas à Diagnostics |
| `diagnostic_reconciliation.from_audit_result` | Durées et compteurs d'inventaire | Corrigé : conservait la durée des chemins mais perdait leurs résultats ; conserve maintenant le résumé `stored_paths` |
| `Diagnostics.add_core_reconciliation_result` | Un terminal existant | Corrigé : rattachement au vrai worker, sans émission de tentative supplémentaire |
| Journal, console et broker Diagnostics | Une occurrence canonique physique, avec détail JSON | Même résumé et même référence ; aucune corrélation par date/chemin |
| Recherche, affichage Diagnostics et export | Détail de l'occurrence canonique | Recherche sur les valeurs stockées ; rendu générique conserve les faits ; JSON/NDJSON identiques |
| `LocalInspection` / Réconciliation CoreUI | Profil sélectionné, IDs et chemins privés, références et candidats locaux | Déjà existant ; aucun changement C. Un candidat n'est ni une restauration ni une autorisation de rattachement |

## Représentation réellement livrée

L'événement existant `library_reconciliation_audit`, phase `finished`, reste un
**résultat d'audit**, pas un résultat de suppression ou de restauration. Le
statut `complete` signifie que l'audit s'est terminé, pas que tous les fichiers
existent. Les anciens événements sans la nouvelle section restent lisibles.

Section optionnelle `detail.stored_paths` :

- `observation_source=core_stored_paths` : origine technique précise.
- `coverage=complete|partial|unverified` : couverture de la vérification des
  chemins, distincte de `inventory_coverage` (inventaire de fichiers).
- `scope=invalidated|not_supplied` : invalide si Core signale un changement de
  profil/portée ; aucun profil valide n'est inventé depuis l'absence d'erreur.
- `reasons` : codes Core observés ; code inconnu qualifié `unclassified_limit`.
- `counts` : uniquement les mesures fournies `stored_path_present`,
  `stored_path_missing`, `stored_path_unavailable`, `stored_path_out_of_scope`.
- `missing_path_context` : mesures fournies `sqlite_present`, `sqlite_missing`,
  `sqlite_other`, `distinct_paths`, `distinct_artifacts_with_id`,
  `distinct_placements_with_id`. Une mesure absente n'est jamais remplacée par 0.

`task_ref` est optionnel, fourni par le contexte réel du worker, conservé en
mémoire et dans les projections héritées. Aucun ID de tâche par profil, index
de ligne ou rapprochement temporel n'est créé. Deux audits restent distincts.

## Matrice de situations et sens des états

| Situation | Observation Core | Qualification diagnostique | Conclusion interdite |
| --- | --- | --- | --- |
| Racine ouverte/stable, ENOENT confirmé | `stored_path_missing` | `missing` pour **ce chemin à cet instant** | Livre supprimé, droit perdu, nettoyage permis |
| Racine absente/non montée | `stored_path_unavailable` | `unavailable` ; raison agrégée disponible | Tous les livres absents |
| Permission refusée, lien/special file, ancêtre instable | `stored_path_unavailable` | `unavailable`, sans cause détaillée si Core ne la fournit pas | Fichier définitivement absent |
| Budget/annulation avant probe | Pas d'état pour la ligne non examinée | `unverified` ; couverture partielle | Ligne non examinée comptée manquante |
| Inventaire partiel mais probe direct complet | Probe ENOENT pour le chemin vérifié | Couvertures séparées : chemins complète / inventaire partiel | Inventaire exhaustif ou absence des autres fichiers |
| Autre profil/hors racine | `stored_path_out_of_scope` | `unverified` pour le profil demandé | Droits/présence empruntés à l'autre profil |
| Profil changé pendant l'audit | `reconciliation_scope_changed` | Audit échoué, portée invalidée, aucune nouvelle preuve | Résultat valable pour le nouveau profil |
| SQLite déjà `missing` | Compteur `sqlite_missing` | Ancien emplacement déjà signalé, distinct d'un actif absent | Suppression physique ou perte récente prouvée |
| Même Artifact dans A/B/C, origines différentes | Historique conservé dans SQLite | Aucun droit ni restauration déduit du SHA ou des compteurs | Restaurer C parce que A/B ont un achat |

Une absence constatée par probe n'est pas une preuve suffisante pour la
réparation : revalidation fraîche, verrous, dépendances et décision utilisateur
appartiennent toujours à A. Une vue agrégée ne permet pas de désigner quelle
ligne est inaccessible ni d'en distinguer le motif exact.

## Secrets et fidélité

Cette projection ne collecte aucun champ libre, chemin, cookie, token, URL ou
payload ADEPT supplémentaire. Les credentials placés dans des champs étrangers
au contrat de mesures ne deviennent pas des événements structurés. Aucun
identifiant ordinaire n'est masqué : **les IDs par ligne ne sont pas fournis par
Core à ce point**, ils ne peuvent donc pas être reconstruits.

Le contrat global du journal brut n'est pas modifié ; pas de réécriture des
anciens journaux ni d'expurgation générale. Permissions locales et confirmation
d'export existantes restent requises. Les tests n'utilisent que des fixtures
synthétiques ; aucun log réel ni secret n'est publié.

## Handoff bloquant vers A : faits par Placement non exposés

Pour satisfaire la représentation **par Placement** de #3638, A doit exposer
les faits de son audit existant après validation de portée. C ne modifie pas
`library_audit_broker.py`, `LibraryAudit`, snapshot, scans ou SQLite.

Contrat minimal proposé, à valider/émettre par A avant intégration C :

| Champ | Autorité exigée |
| --- | --- |
| `identity_id` / profil | Profil effectivement inspecté, revalidé avant remise des faits ; jamais profil courant lu après changement |
| `placement_id`, `artifact_id` | Valeurs attestées de la ligne SQLite du snapshot, facultatives si réellement absentes ; jamais recomposées depuis SHA/chemin |
| `path` | Chemin enregistré de cette ligne, uniquement sans credential ; l'absence de chemin n'efface pas les IDs |
| `state=missing|unavailable|unverified` | Mapping du probe réellement effectué ; ligne non examinée explicitement non vérifiée |
| `observation_source` / preuve | Référence Core du snapshot et du probe existants, fraîcheur/instant, pas un index seul réutilisable entre profils |
| Couverture et portée | Couverture du probe distincte du scan global ; budget/annulation/changement de profil explicites |
| Motif | ENOENT, racine indisponible, permission, hors périmètre ou motif inconnu, seulement si observé ; la cause détaillée n'est pas fournie actuellement |
| Restauration | `not_evaluated` par défaut ; candidat fournisseur seulement si politique/source/droit attestés par Core ; transfert ponctuel sans droit fournisseur ne confère rien |

Émettre via un point Core/Diagnostics coordonné, sans seconde boucle de scan ni
second journal. Pour les lots bornés, fournir la couverture et le nombre de
lignes non remises sans les déclarer absentes. Préserver Artifact, OriginLink,
SourceReference et liens ADEPT ; zéro mutation en observation.

**Décision de réception : ce lot corrige la perte agrégée et sa corrélation,
mais ne prouve pas la propagation des IDs par ligne. #3638 reste ouvert tant
que ce handoff n'est pas intégré/testé. #2847 et #3185 ne sont pas clos.**

## Vérification reproductible

`tests/test_diagnostics_missing_placements.py` réutilise les vrais probes Core,
le formatter/filtre/journal privés, le broker et les exports existants. SQLite
v14 synthétique : deux achats, un Artifact commun, trois profils, cinq liens
(deux fournisseur, deux ADEPT et un transfert), fichiers jamais supprimés.
Après préparation de la fixture, comparaison octet à octet de la base et des
fichiers et comparaison de toutes les lignes d'historique après observation.
Le plan Core confirme que le transfert C ne devient pas un droit de restauration.
Ce sont des tests d'intégration ; pas un E2E GTK ni une recette de réparation.

```sh
PYTHONPATH=src python -m pytest -q \
  tests/test_diagnostics_missing_placements.py \
  tests/test_core_p0_2730_reconciliation_diagnostics.py \
  tests/test_core_p1_2900_durable_reconciliation_audit.py \
  tests/test_library_stored_paths.py \
  tests/test_core_p0_2847_missing_path_reference_preview.py
```

La PR et #3638 consignent les nombres de tests, HEAD et liens CI exacts.

## Checklist de recette sur copie isolée #3607

1. Utiliser le dispositif jetable existant #3607. Relever SHA/config/profil et
   l'identité de la copie, garde de confinement active. Ne jamais lancer
   cette recette sur la bibliothèque de production.
2. Préparer les seuls jeux synthétiques/copies autorisés : chemin jamais créé,
   racine indisponible, permission refusée, budget partiel, profils A/B de même
   SHA, achat fournisseur conservé et copie ponctuelle C. Aucun déplacement,
   effacement ou restauration de fichier utilisateur.
3. Capturer les références et empreintes SQLite/FS avant observation ; conserver
   séparément les OriginLink/SourceReference/ADEPT. Distinguer les préparatifs
   de fixture des vérifications en lecture seule.
4. Lancer **l'audit existant** du profil choisi, puis consultation Diagnostics.
   Vérifier les états/couvertures de la matrice ; changer de profil pendant un
   audit contrôlé doit invalider sa portée. Aucun scan C supplémentaire.
5. Comparer l'occurrence par `task_ref`, journal, recherche, détail affiché et
   export confirmé. Relever les IDs **seulement s'ils sont émis par A** ; sinon
   marquer le critère non vérifié. Ne jamais accepter un compteur comme ID.
6. Refaire les comparaisons SQLite/FS : aucun fichier ni historique modifié,
   aucun droit effacé/fusionné, aucun téléchargement ; A/B/C restent séparés.
7. Relever PASS/FAIL/non exécuté et fermer la copie via le dispositif existant.
   Toute recette des réparations #2847 reste une opération distincte d'A.

Aucune recette sur données utilisateur n'a été exécutée par C pour ce ticket.
