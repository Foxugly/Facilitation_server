# Timer de round — conception

**Date :** 2026-07-18
**Portée :** `Poker_server` (modèles, services temps réel, consumer) + `Poker_frontend` (affichage du décompte, réglage facilitateur)

## Le problème

Aujourd'hui un round de vote n'a aucune limite de temps, et `reveal()` n'exige qu'**un seul vote**
(`realtime/services.py:180`) : le facilitateur peut révéler à 1 voix sur 12. Il n'existe ni décompte,
ni relance, ni fermeture automatique. Un participant absent est un vote perdu, silencieusement.

## Décision

Un timer **optionnel**, contrôlé par le facilitateur.

- Le facilitateur active ou désactive le timer, et règle sa durée.
- À l'ouverture du vote, le serveur calcule l'échéance et la diffuse ; le client affiche le décompte.
- À zéro, le **serveur** gèle les votes et révèle. Le client ne décide de rien : il affiche.

Défaut : **désactivé**, durée **10 s** quand on l'active. Durées admises : **10 à 60 secondes, par pas
de 5** — soit onze valeurs (10, 15, 20 … 60). Le pas contraint l'UI à un sélecteur simple plutôt qu'à
une saisie libre, et évite les durées arbitraires d'un client modifié.

## Autorité du serveur

Le décompte affiché par le client est **cosmétique**. L'échéance fait foi côté serveur, pour trois
raisons : les horloges des participants divergent, un client peut être modifié, et un client
déconnecté ne doit pas empêcher la révélation.

Concrètement : `VoteSession.vote_deadline` (nullable) est écrit à l'ouverture et diffusé dans
`vote.opened`. Toute tentative de vote après l'échéance est refusée par le serveur, même si un client
retardataire affiche encore « 2 s ».

## Déclenchement à zéro

Channels tourne en ASGI, donc sur une boucle asyncio : une tâche `asyncio` programmée dans le
processus suffit, **sans Celery ni worker séparé**.

Deux garde-fous, parce qu'une tâche en mémoire n'est pas fiable seule :

1. **La tâche est rattachée à la room, pas au consumer d'un client.** Sinon elle meurt quand ce client
   se déconnecte — précisément le cas de celui qui ferme son portable.
2. **Réconciliation paresseuse.** L'échéance étant en base, tout point d'entrée qui touche la session
   (`state.sync` à la reconnexion, `vote.cast`, `vote.reveal`) vérifie si elle est dépassée alors que
   l'état est encore `open` ; si oui, il révèle immédiatement. Un redémarrage du service — et la flotte
   auto-déploie, donc ça arrive — ne peut donc pas laisser un round gelé indéfiniment.

La tâche asyncio est un confort qui rend la révélation instantanée dans le cas nominal ; la base est la
source de vérité.

## Protocole temps réel

Le protocole est versionné (`PROTOCOL_VERSION = 1`). Les ajouts sont rétrocompatibles : un client
ancien ignore les champs qu'il ne connaît pas et continue de fonctionner sans timer.

**Nouvelle intention client** (facilitateur uniquement, comme `vote.open`) :

- `timer.set` → `{enabled: bool, seconds: int}`. Persisté sur la `Room`, donc conservé d'un round à
  l'autre. Le serveur arrondit au multiple de 5 le plus proche puis borne à 10–60 s.

**Champs ajoutés aux événements existants :**

- `vote.opened` porte `deadline` (ISO 8601) ou `null` si le timer est désactivé.
- `state.sync` porte le même `deadline` pour que celui qui rejoint en cours de round voie le temps
  restant.

**Nouvel événement serveur :**

- `timer.changed` → `{enabled, seconds}`, diffusé à toute la room quand le facilitateur modifie le
  réglage, pour que chacun voie la même configuration.

La révélation à échéance réutilise l'événement `vote.revealed` **existant**, enrichi d'un
`reason: "timeout" | "facilitator"`. Pas de nouvel événement de révélation : les clients savent déjà
traiter `vote.revealed`, et un second chemin dupliquerait la logique d'affichage.

## Ce qui ne change pas

- Le secret des votes jusqu'au reveal.
- Le rôle du facilitateur et son transfert.
- La possibilité de révéler manuellement avant l'échéance.
- L'absence de quorum : révéler reste possible avec un seul vote. Le timer traite le temps, pas la
  participation — mêler les deux dans un même lot brouillerait les deux sujets.

## Hors périmètre

- Le quorum et la fermeture automatique quand tout le monde a voté (candidat naturel pour la suite).
- Un timer par sujet plutôt que par room.
- Toute relance ou notification vers les absents.
