# Service LLM Core (optionnel, expérimental)

Aucun fournisseur de modèle, appel réseau ni clé n'est requis pour utiliser Cifereca.
Par défaut `llm.enabled=false` et `context.service("llm") is None` : les
plugins **doivent** conserver leur fonctionnement complet hors LLM.

## Exemple local explicite (serveur compatible OpenAI déjà installé)

Dans le fichier de configuration Core JSON :

```json
{
  "llm": {
    "enabled": true,
    "provider": "openai-compatible",
    "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
    "model": "modele-local",
    "allow_remote": false,
    "api_key_env": "",
    "timeout_seconds": 5,
    "max_input_chars": 4000,
    "max_output_tokens": 256
  }
}
```

La configuration ci-dessus **ne lance pas** de serveur ; elle autorise seulement
un appel lorsqu'un plugin le demande explicitement. L'endpoint doit être un
serveur parlant l'API de chat completions compatible OpenAI. Le Core ne dépend
d'aucune bibliothèque fournisseur. Les seuls hôtes reconnus comme locaux
sont `localhost` et les adresses IP loopback.

Pour un endpoint distant, configurer **explicitement**
`enabled=true`, une URL HTTPS et `allow_remote=true`.
C'est l'autorisation d'envoyer les prompts/documents aux systèmes distants.
Les redirections HTTP et les URL comportant des identifiants, query strings ou
fragments sont refusées. Ne pas inscrire de token dans l'URL ou la configuration :
`api_key_env` désigne **le nom** d'une variable d'environnement déjà définie.

## Plugin

```python
from cifereca.plugin_api import LLMRequest

llm = context.service("llm")
if llm is not None and llm.available():
    result = llm.generate(
        LLMRequest("Résume ce bref extrait déjà sélectionné", max_output_tokens=100),
        control=control,  # facultatif : TaskControl
    )
    if result.ok:
        print(result.text)  # contenu généré, non attesté
```

`LLMResult` indique `generated=True`, `provider`, `model`, `remote` et,
en mode `task="structured"`, un objet JSON dans `structured`. Aucun résultat
LLM ne fait foi pour les âges attestés, la provenance bibliographique, le DRM,
la propriété ou les droits d'accès.

Un backend indisponible, une annulation ou un timeout retourne `ok=False`
avec un `reason` non sensible : **jamais de prompt, réponse, exception HTTP
ou secret**. Une file bornée de deux workers permet de ne pas bloquer le Core ;
une requête réseau déjà lancée ne peut pas être interrompue physiquement et
peut continuer au plus jusqu'à la fin de son thread daemon. L'appelant reçoit
cependant un timeout/une annulation sans attendre la fin de ce thread. Le
timeout maximum est 15 secondes, la sortie est limitée à 32 KiB et le prompt
à 16 000 caractères. Aucun retry, embedding ou outil autonome en v1.

Pour les plugins utilisant GTK, lancer `generate` via le TaskRunner/broker de
tâches, **jamais sur le fil principal GTK**.

## Configuration graphique — Paramètres → Intelligence artificielle

La page **Intelligence artificielle** configure le service LLM commun aux plugins.
L’interrupteur est désactivé par défaut. Seule l’API Chat Completions compatible OpenAI
est prise en charge, sur un serveur déjà installé.

1. Activer la fonction et renseigner le modèle et l’endpoint complet. Le bouton
   de préremplissage Ollama local renseigne uniquement l’adresse loopback ; il
   n’installe ni ne démarre aucun modèle.
2. Pour une destination distante : endpoint HTTPS et option « Autoriser les
   requêtes vers un serveur distant ». Toute modification de l’endpoint retire
   cette autorisation jusqu’à un nouveau choix. Diagnostics demande en plus
   son propre consentement explicite, **par analyse**.
3. Si le serveur requiert une clé, renseigner uniquement **le nom** d’une
   variable d’environnement du processus. La valeur secrète ne doit jamais
   être saisie dans Cifereca ni enregistrée dans le fichier de configuration.
4. Enregistrer via Paramètres puis **redémarrer Cifereca** : le service Core
   et les plugins utilisant son instance existante ne sont pas rechargés à chaud.

« Tester la connexion » est une action explicite : une requête synthétique
constante est envoyée sans livre ni diagnostic, sur un thread de tâche séparé.
Un premier essai distant demande une confirmation supplémentaire indiquant
l’hôte, le modèle et le risque de facturation. Le résultat « Configuré »
ne remplace jamais « Test réussi » ; un test passé ne garantit pas la
disponibilité future du service ni n’autorise des appels automatiques.

Une IA indisponible ne bloque pas le reste de Cifereca. Aucun réseau n’est
contacté en ouvrant la page ou en sauvegardant les paramètres.


## Configurations nommées (P0 #8465)

Le format historique plat reste lisible et utilisable. L'action explicite de
gestion des profils dans Paramètres migre les valeurs vers un schéma versionné :
une activation globale, des profils nommés (32 au plus), un identifiant
sélectionné ou aucun, et une autorisation distante liée à la destination exacte.
Il n'existe ni profil de secours ni duplication mutable du profil actif.

Dans le formulaire IA, saisir un nom de profil (lettres ASCII, chiffres, tiret
ou underscore, 64 caractères au maximum), puis « Créer un profil » à partir
des champs du formulaire, ou sélectionner/supprimer un nom existant. La liste
et le profil sélectionné sont affichés. Créer un profil copie les champs
courants sans modifier le profil précédent ni transférer son autorisation.
Modifier les champs du profil sélectionné,
puis enregistrer. Les modifications restent dans le brouillon avant Save ;
« Annuler les modifications » restaure la configuration sauvegardée. Toute
sélection différente désactive le service et révoque l'autorisation distante.
Une ancienne autorisation distante n'est pas transférée lors de la migration.

L'autorisation distante enregistrée lie le nom du profil, le protocole,
l'endpoint complet (port et chemin inclus), le modèle et la référence de clé.
Une modification de ces données révoque l'accord ; les confirmations par
analyse des plugins restent obligatoires. La valeur de clé ne doit jamais
figurer dans les profils. Le processus courant peut refuser un service révoqué
mais n'active jamais le nouveau profil à chaud. Enregistrer et redémarrer
pour construire la nouvelle instance. Une requête déjà transmise ne peut
pas être rappelée ; son résultat est écarté si l'instance est révoquée
avant sa remise à l'appelant.

Le Core maintient des compteurs bornés en mémoire par catégorie de résultat
et destination locale/distante (nombre, durée cumulée et maximale).
Pas de journal de contenu, de clé, de modèle ou d'URL, ni de comptabilité
financière. Les compteurs disparaissent au redémarrage.


## Réception technique et terrain

Les scénarios existants `llm-settings-1366` et `llm-settings-1600`
vérifient les opérations sur les profils dans un XDG jetable, puis démarrent
un nouveau processus avec le seul vrai plugin Diagnostics activé. Les deux
requêtes texte/JSON vont vers une fixture HTTP loopback synthétique.
Après désactivation, Diagnostics reste activable sans service IA.
Les valeurs de clé, prompts utilisateur et journaux réels ne font pas
partie de ces recettes.

Ce banc ne qualifie ni la qualité d'un modèle d'inférence, ni un serveur
HTTPS distant réel, ni un lecteur d'écran AT-SPI. Pour les recettes terrain,
utiliser une installation isolée, un modèle effectivement disponible et
des données synthétiques ; contrôler texte, JSON objet, indisponibilité,
annulation et absence de fallback. Pour le distant, autorisation
propriétaire explicite préalable, endpoint HTTPS maîtrisé, confirmation
du coût éventuel et secret fourni uniquement par environnement.
Vérifier également le refus après modification de destination et le
fonctionnement du plugin sans IA. Ne pas mettre URL réelle, clé,
prompt, réponse ou données de livre dans les artefacts publiés.
