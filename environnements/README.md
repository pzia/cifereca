# Environnements Cifereca

Ce répertoire contient des **recettes reproductibles de mise en place** pour
des environnements d'exécution ou de test particuliers.

Une recette peut fournir du bootstrap rootless, des lanceurs, des tests de
validation et des outils headless. Les caches de paquets, environnements
virtuels, captures et autres artefacts générés ne sont pas des sources et ne
doivent pas être versionnés.

Recettes présentes :

- `chatgpt-work/` : environnement ChatGPT Work Ubuntu 24.04 existant ;
- `openai-chat-debian/` : environnement OpenAI Chat Debian 13 (trixie),
  GTK4/libadwaita rootless et exécution headless.

Les recettes doivent rester indépendantes du système global autant que
possible et documenter précisément leurs prérequis et leur procédure offline.
