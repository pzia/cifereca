# Diagnostics P0 #1009 — analyse IA facultative

**Aucune requête au modèle à l'ouverture de la page Diagnostics.** Le
service reste le service Core `context.service("llm")` (#778), configuré
explicitement avec `llm.enabled=true`. Aucun nouveau moteur IA, agent ou
accès disque n'est introduit.

Parcours : **Prévisualiser pour l'IA** (lecture locale via broker public des
événements expurgés, maximum 24, 3 000 caractères), vérifier le texte exact
et la destination déclarée (local/distant, hôte, modèle), puis **Autoriser
et analyser**. La deuxième action est obligatoire **pour chaque analyse**
et le consentement est invalidé si la destination change ou si les filtres
source/période changent. `Annuler` désactive le consentement et envoie une
annulation coopérative ; un appel distant déjà lancé ne peut pas être
effacé rétroactivement. Aucune transmission de fichier brut ou de logs
libres non expurgés.

Le résultat indique explicitement **inférence IA non vérifiée** et demande
des preuves avec références d'événements, contradictions, hypothèses et
contrôles non destructifs. Les traces sont des **données non fiables**,
jamais des instructions pour le modèle. L'absence de LLM, de données,
ou un refus/timeout/annulation n'affecte pas l'explorateur ni le rapport
support déterministe. Aucun résultat n'est mis en cache ni journalisé.
La copie du résultat est une action utilisateur distincte.

**Limites P0 :** uniquement les deux sources du broker Core
(`core_events`, `app_log`). Pas encore d'import manuel de fichiers, de
corrélation prouvée entre acquisitions ou de synthèse IA multi-lots.
Pas de preuve de cause par simple code ADEPT/PNB. La recette UX GTK reste
à réaliser en dehors des archives historiques et de la CI.

Aucune nouvelle GitHub Action ; les tests utilisent un modèle simulé
sans connexion réseau.

## Durcissement P0 — consentement à usage unique et références

Chaque prévisualisation émet désormais un **jeton aléatoire distinct**, conservé
uniquement en mémoire dans un état de consentement verrouillé. Le jeton
expire après **5 minutes** et devient inutilisable immédiatement avant
l'appel au modèle : double clic, deux threads, copie de la prévisualisation,
timeout ou erreur ne peuvent pas réutiliser la même autorisation. Toute
altération du texte prévisualisé, de la destination ou de la liste des
références invalide cette autorisation. Refaire une analyse implique une
**nouvelle prévisualisation et une nouvelle confirmation**.

Avant l'envoi au modèle, les **métadonnées d'événement** sont projetées
une seconde fois sur un contrat fermé : source Core connue, module technique
reconnu (sinon masqué), sévérité/statut autorisés, horodatage ISO avec fuseau,
identifiant au format public attendu. Un identifiant ne peut être cité comme
preuve si la source ou sa date sont inconnues. Les messages techniques restent
soumis à l'expurgation Core complète avant la limite de taille du prompt.
La dernière date d'une source inconnue n'est pas attribuée arbitrairement.
L'utilisateur doit toujours contrôler l'aperçu avant un éventuel envoi distant.

Les références citées par le texte généré sont désormais comparées aux
identifiants exacts des événements effectivement transmis. Le rapport
sépare « référence retrouvée dans l’extrait » et « référence inconnue ».
**Même une référence retrouvée ne valide aucun raisonnement ni causalité**
et les hypothèses du modèle restent explicitement non vérifiées. Le texte
de sortie du LLM est expurgé **avant toute troncation** afin de ne pas
laisser passer des préfixes d'URL ou de jetons coupés.

Limitations conservées : la liste des événements est un extrait borné, pas
une archive exhaustive ; le service LLM Core existant peut terminer un
appel distant déjà émis malgré l'annulation de l'interface. Aucun
nouveau service LLM, collecte réseau, journal de prompts ni workflow CI.


## Journal physique et références IA (#3133)

La prévisualisation IA des Diagnostics utilise `source="journal"` par
défaut, comme l'explorateur. Les `event_id`, horodatages et `source`
authentiques du journal restent présents dans la preuve filtrée (sans
reconstruire de lien par heuristique temporelle). Le collecteur structuré
facultatif provient du même journal dans les Core compatibles. L'IA demeure
**strictement sur consentement préalable** : aucun appel réseau ni modèle
au chargement de la page.
