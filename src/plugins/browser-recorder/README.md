# Browser Recorder — outil technique opt-in (0.1.0)

Plugin **désactivé par défaut**. Activez-le dans **Plugins**, puis ouvrez **Browser Recorder** (section Configuration).

## Pré-requis

Le plugin ne télécharge **pas** automatiquement un navigateur ni des dépendances. Sur la machine Linux qui exécute Cifereca (avec une session graphique), installez volontairement :

```bash
python -m pip install 'playwright>=1.50,<2'
python -m playwright install chromium
```

Attention : Playwright peut nécessiter des bibliothèques système Chromium supplémentaires. Le paquet n'impose pas Playwright au Core et les tests hors ligne ne requièrent aucun navigateur réel.

## Capture

1. Saisir l’**URL HTTPS** de départ d'un site autorisé. Sites localhost, IP littérales et URL contenant des identifiants refusés.
2. Cocher le consentement puis **Démarrer la capture**. Chromium est ouvert dans un contexte **éphémère**, sans profil/session persistante et sans téléchargements.
3. Se connecter **manuellement** (SSO/MFA normaux) et naviguer. Le plugin n'enregistre ni frappe, ni DOM, ni capture écran, ni cookies/tokens, ni corps, ni en-têtes HTTP. Il n'utilise **pas** la fonction HAR brute de Playwright.
4. Cliquer **Arrêter / enregistrer** : un fichier `.har` privé (mode 0600) est produit dans le répertoire des données du plugin, sous `captures/`. **Aucune synchronisation ni publication**.

L'export est un **HAR 1.2 limité aux métadonnées**, pas une archive de trafic permettant de reproduire une requête : méthodes HTTP, codes de retour et formes d'URL du seul site HTTPS ciblé. Les paramètres de requête et fragments sont intégralement supprimés ; les segments de chemins qui ne sont pas des mots génériques autorisés deviennent `~redacted~`. Les redirections externes ne sont pas exportées. Seuls **300 événements maximum** sont conservés ; la sauvegarde refuse au-delà de 256 Kio. Le site choisi et les heures restent visibles et peuvent être sensibles : **ne pas publier les captures sans revue**.

## Rejeu sous supervision

Dans l'interface, conserver le même site HTTPS et renseigner le nom `.har` (automatiquement rempli après la capture). **Rejouer** ouvre une nouvelle session vierge et visite la page d'accueil du site. Refaire l'authentification **manuellement** ; chaque clic sur **Étape GET suivante** autorise **une** navigation vers un chemin statique explicitement autorisé. **Arrêter** interrompt le parcours. Les URL dynamiques expurgées et requêtes avec query sont ignorées ; **aucune mutation, POST, authentification automatique, achat ni téléchargement ne sont rejoués**. Le rejeu est donc volontairement **partiel** si le fournisseur dépend d'URL signées, d'étapes POST ou de chemins identifiants.

## Limites de confiance

Le navigateur peut toujours afficher des informations privées et contacter des hôtes tiers **dans la session** pendant une connexion normale ; ce n'est pas un proxy de sécurité et ne bloque pas le trafic réel. Le plugin ne collecte aucun de ces contenus tiers dans l'export. Les sessions sont terminées à l'arrêt ; pas de persistance de mots de passe ou d'état Playwright. Fermer Cifereca termine une capture en cours, sans la sauvegarder si l'arrêt explicite n'a pas eu lieu. Les tests automatisés emploient uniquement des faux objets Playwright ; aucune authentification fournisseur réelle n'est revendiquée.
