# #4982 — politique de rotation publique du dispatcher

Statut : contrat et fixtures autonomes sur main, **non raccordés au pump**.
Références #6558, #6559, #6566, #6573, #6596, #6599, #6602.

La politique actuelle du pump en branches utilise un seul curseur de clés,
et le même retour None du bridge pour doublon local et capacité pleine.
Elle ne prouve donc pas l'équité entre quatre types ni le passage au
candidat suivant si le premier est déjà soumis et non encore claimé.

## Politique proposée

- Quatre classes fermées : fetch, reparse, relink, recompute.
  Seules les classes ayant au moins une implémentation explicitement
  enregistrée participent. Un curseur opaque par classe et un rotor,
  pas de file, liste de jobs conservée entre ticks, timer ou exécuteur.
- Au plus 32 lignes examinées au total par tick, réparties entre classes
  actives (8 chacune si quatre). Utiliser exactement les lectures
  `read_ready_page` avec filtre des couples kind/implementation de
  cette classe. Chaque lecture peut examiner des lignes d'autres classes :
  elles comptent aussi dans le quota. Les pages vides progressent.
- Soumettre une référence par classe à tour de rôle, au plus deux
  handles réellement acceptés par tick. Les hints restent bornés
  et éphémères ; les claims et CAS restent dans le worker existant.
- Distinguer accepted(handle), duplicate(ref déjà en vol localement)
  et blocked(capacité ou broker fermé). Un doublon exact ne consomme
  pas un slot et ne bloque pas les autres candidats. Une vraie
  saturation conserve le premier candidat non soumis dans sa classe ;
  cette classe ouvre le tour suivant. Une annulation ne fait pas
  avancer un candidat non offert.
- Les tâches conservent la priorité METADATA et le budget partagé ;
  aucune promotion sur une priorité utilisateur. Cela évite une
  famine entre classes de travaux C sous créneaux disponibles,
  pas la famine globale causée par une saturation permanente des
  lanes utilisateurs/fournisseurs.

## Raccordement après fusion des prérequis

1. Construire PublicDispatchRotation depuis les classes réellement
   couvertes par le registre immuable du pump (pas de fetch implicite
   ni activation d'un provider parce qu'un job existe).
2. Sous le mutex de tick existant, adapter read_ready_page en RotationPage,
   réutiliser la même horloge par tick et comptabiliser tous les scans.
3. Dans AggregateWorkBridge, distinguer doublon exact (sous son mutex)
   et plafond de soumissions/refus d'admission atomiquement dans
   le même chemin submit. Conserver l'API submit publique existante
   si nécessaire via wrapper ; jamais de check-then-act séparé pour
   la qualification du doublon. Adapter les retours en RotationOffer.
4. Publier les compteurs agrégés de tick existants, conserver l'état
   busy et annulation. Les compteurs de corruption sont des occurrences
   de scan, pas un nombre de clés distinctes.
5. Tester le vrai pump et broker sur main intégré, quatre classes,
   flux continu, rare, capacité 1/2 et 1 slot utilisateur réservé,
   génération remplacée et dépendances. Mesurer le coût des snapshots
   séparés ; ne pas augmenter le quota pour masquer la latence.

## Limites

La rotation reste par classe et clé publique, pas par fournisseur,
scope privé/compte ni ordre d'ancienneté intra-classe. Un burst de
nouveaux travaux devant le curseur n'a pas de garantie SLA ; la
progression décrite suppose des ticks répétés, des slots disponibles
et une clé continuellement éligible dans un journal borné. L'équité
entre scopes privés nécessite leur future admission exacte et la
qualification des ports. Les tests de ce lot utilisent des pages
synthétiques et le budget réel SharedTaskAdmission, sans exécuter
les producteurs. Pas de résultat positif prétendu sur main des ports
encore en PR. Aucun nouveau GET, writer, workflow ou privilège.
