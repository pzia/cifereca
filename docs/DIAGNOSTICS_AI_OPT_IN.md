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
