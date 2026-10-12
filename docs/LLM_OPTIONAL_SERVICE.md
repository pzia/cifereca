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
