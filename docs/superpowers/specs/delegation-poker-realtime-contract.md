# Contrat temps réel — Delegation Poker Online, Phase 1

**Date :** 2026-07-07
**Portée :** Phase 1 (salle anonyme, temps réel, Delegation Poker). Transport : Django Channels + Redis.
**Lié à :** `delegation-poker-scope.md`, `delegation-poker-design-phase1.md`.

> Ce document fige le **protocole** entre le front Angular et le back Channels : frontière
> HTTP/WS, enveloppe des messages, événements dans les deux sens, snapshot d'état, autorité,
> cas limites. Il est le préalable au **modèle de données détaillé** et au **plan d'implémentation**.

---

## 0. Principes (actés)

| # | Principe | Décision |
|---|----------|----------|
| 1 | **Serveur = source de vérité** | Le client émet des *intentions* ; le serveur décide et **rediffuse le fait** à tous. Pas d'affichage optimiste : le client attend l'écho serveur. |
| 2 | **Autorité facilitateur** | Les événements de contrôle (`vote.open/reveal/reset`, `result.act`, `item.*`, `round.*`, `deck.select`, `timer.set`, `reveal.setMode`, `facilitator.transfer`) ne sont acceptés **que** du facilitateur. `response.cast` fait exception : ouvert à tous (§8.2.a), ce n'est pas une intention de contrôle. Le serveur **rejette** sinon (le masquage front n'est qu'un confort). |
| 3 | **Rôle porté par le token, pas par la connexion** | À la reconnexion, token → participant → rôle + vote restaurés. Une coupure ne perd pas le rôle. |
| 4 | **Secret réel des votes** | Aucune valeur de vote n'est diffusée avant `reveal`. Avant : seulement « a voté / pas voté ». |
| 5 | **HTTP crée/résout la salle ; WS gère la vie dans la salle** | Le socket ne s'ouvre qu'une fois *dans* la salle. |

---

## 1. Frontière HTTP ↔ WebSocket

**HTTP (REST, convention flotte)** — avant d'ouvrir le socket. Les routes sont montées sous
`/api/v1/` (`config/urls.py` + `rooms/api_urls.py`) — pas `/api/rooms` (forme Phase 1 jamais
implémentée telle quelle) :

| Méthode | Route | Corps | Retour |
|---------|-------|-------|--------|
| `POST` | `/api/v1/rooms` | `{ title?, username?, team? }` | **201** `{ code, roomTitle, participantToken, role: "facilitator", deckSnapshot, availableDecks, isTeam }` |
| `POST` | `/api/v1/rooms/{code}/join` | `{ username? }` (ignoré si salle d'équipe : rôle dérivé du compte connecté) | **200** `{ code, roomTitle, participantToken, role: "voter", deckSnapshot, availableDecks, isTeam }` — **404** `{ detail }` si salle inconnue/expirée |
| `GET` | `/api/v1/rooms/{code}` | — | Résout l'existence d'une salle (arrivée par URL) : `{ code, roomTitle, exists, isTeam }` |

- Le **`participantToken`** est un **secret aléatoire** généré serveur (long, non devinable). Le client le stocke en `localStorage` **à côté du username** et le rejoue à chaque (re)connexion WS.
- Le **rôle vit côté serveur** (table `token → rôle` dans l'état de salle). Le client **ne s'auto-déclare jamais** facilitateur ; il n'envoie que son token.
- Le **`deckSnapshot`** est immuable pour la durée de la salle (voir scope §3.6). `availableDecks`
  (Phase 2) porte en plus tout le catalogue frozen jouable par cette salle (`Room.deck_snapshots`).
- **Salle d'équipe** (`team` fourni à la création, Phase 2) : compte requis (**401**
  `auth_required` sinon), membre requis (**403** `not_a_member` sinon) ; `username` est ignoré,
  le nom d'affichage vient du profil du compte. **Salle anonyme** : `username` obligatoire
  (**400** `username_required` si absent/vide). Les deux routes de création/entrée refusent
  aussi **403** `room_full` au-delà de `Room.max_participants` (`rooms/api_views.py`).

**WebSocket** — tout ce qui se passe *dans* la salle (§4, §5). Endpoint : `wss://…/ws/rooms/{code}/`. Premier message client obligatoire : `session.join` (§4).

---

## 2. Enveloppe des messages

Tous les messages (deux sens) partagent une enveloppe **versionnée** :

```json
{ "v": 1, "type": "response.cast", "payload": { }, "cid": "c-8f3a", "ts": 1720353600 }
```

- **`v`** — version de protocole. Le serveur **rejette proprement** (`error` type `protocol.version`) une version qu'il ne comprend pas. Front et back se déployant séparément (repos distincts), ce champ évite les casses silencieuses.
- **`type`** — nom d'événement (§4/§5).
- **`payload`** — données de l'événement.
- **`cid`** — identifiant de corrélation généré client (idempotence + rapprochement requête/écho).
- **`ts`** — horodatage émetteur (informatif).

---

## 3. Identité, rôles, présence

> **Note « username ».** Dans ce contrat, `username` = **nom d'affichage éphémère** du participant
> (anonyme, non authentifiant). Il ne correspond **pas** à un identifiant d'auth : la flotte
> authentifie par **email uniquement** (§3.16 ops, pas de champ `username`). En Phase 2, un membre
> authentifié se connecte par email et porte un nom d'affichage distinct.

- **Deux rôles** : `facilitator` (= le **créateur**, un seul rôle de contrôle à la fois) et `voter`. Le transfert *volontaire* de rôle (`facilitator.transfer`, §4) est **implémenté** (Phase 2) ; le **garde-fou** (§6.f, `facilitator.claim`) reste la voie de secours en cas d'absence, pas la seule.
- **Un token = un participant.** Deux onglets sous le même `localStorage` (même token) → **un seul participant** ; la nouvelle connexion **remplace** l'ancienne (le serveur rattache le token existant, ne crée pas de doublon).
- **Présence** : le serveur suit l'état connecté/déconnecté de chaque participant et diffuse les changements (`participant.joined` / `participant.left`), sans jamais divulguer de valeur de vote.

---

## 4. Événements client → serveur (intentions)

| `type` | Émetteur autorisé | `payload` | Effet |
|--------|-------------------|-----------|-------|
| `session.join` | tous | `{ participantToken }` | (Re)entrée dans la salle. Le serveur répond **au seul client** par `state.sync` (§5), et diffuse `participant.joined` aux autres. |
| `vote.open` | facilitateur | `{ }` | Ouvre le tour (`idle → open`). Refusé si pas de sujet. |
| `vote.reveal` | facilitateur | `{ }` | `open → revealed`. Autorisé dès **≥ 1 vote** (pas de quorum). |
| `result.act` | facilitateur | `{ chosenValue }` | `revealed → acted`. Fige le résultat retenu (défaut proposé = mode/médiane, modifiable). |
| `vote.reset` | facilitateur | `{ }` | Efface les réponses du round courant, le remet à `idle`. |
| `facilitator.claim` | tout participant présent | `{ }` | **Uniquement** si le garde-fou est actif (§6.f). Premier arrivé = nouveau facilitateur. |
| `round.prepare` | facilitateur | `{ subjectId?, subjectText?, anonymous?, deckId?, timerEnabled?, timerSeconds? }` | Compose et annonce le round suivant **en un seul appel atomique** (sujet/round, deck, mode de révélation, timer), le laisse `idle` (n'ouvre pas). Refusé si le round choisi a déjà démarré. |
| `deck.select` | facilitateur | `{ deckId }` | Change le deck actif de la salle. Refusé tant qu'un round est `open`/`revealed` (les réponses déjà émises référencent le deck courant). |
| `timer.set` | facilitateur | `{ enabled, seconds }` | Règle le timer de la salle (durée normalisée 10–60 s par pas de 5). **Fonctionnalité d'équipe** : refusé (`forbidden.subscription_required`) sur une salle anonyme. |
| `reveal.setMode` | facilitateur | `{ anonymous }` | Fige le mode de révélation du round courant, **tant qu'il est `idle`** (les votants doivent le connaître avant de voter). Le mode anonyme est une option d'équipe payante : refusé (`forbidden.subscription_required`) sinon. |
| `facilitator.transfer` | facilitateur | `{ targetParticipantId }` | Transfert **volontaire** du rôle à un autre participant présent ; l'ancien facilitateur redevient votant. Contrairement à ce que §9 affirmait à l'origine, **ceci est implémenté**, pas hors périmètre. |

> Cette table date de la Phase 1 (2026-07-07) et décrivait aussi `subject.set` et
> `vote.cast`. Les deux sont remplacés — le premier par `item.add`/`item.update`/
> `round.select` (§8.1.a), le second par `response.cast` (§8.2.a) — et **retirés** :
> `Facilitation_frontend` n'en a plus besoin, vérifié en prod (§8.1.b, §8.2.b). Les cinq
> dernières lignes (`round.prepare` à `facilitator.transfer`) sont des ajouts Phase 2
> (équipes, decks multiples, transfert volontaire) absents de la table d'origine.

Toute intention **incohérente avec l'état courant** (ex. `response.cast` hors `open`) est
**rejetée** par `error`, pas appliquée (§6.b).

---

## 5. Événements serveur → clients (faits)

| `type` | Cible | `payload` |
|--------|-------|-----------|
| `state.sync` | **1 client** (join/reconnect) | Snapshot complet (§5.1). |
| `participant.joined` | tous | `{ participantId, username, role }` |
| `participant.left` | tous | `{ participantId }` |
| `participation.update` | tous | `{ voted: number, total: number, votedIds: string[] }` — **jamais de valeurs** |
| `subject.updated` | tous | `{ text }` |
| `vote.opened` | tous | `{ }` (état → `open`) |
| `vote.revealed` | tous | `{ itemResults: [...] }` (§8.2.a) — décompte **par item**, jamais de lien participant → carte sur un round anonyme. Seules les valeurs ayant ≥ 1 voix figurent, dans l'ordre du deck. Porte aussi `reason: "timeout" \| "facilitator"`. |
| `result.acted` | tous | `{ chosenValue }` (état → `acted`) |
| `vote.wasReset` | tous | `{ nextState: "idle" }` |
| `facilitator.changed` | tous | `{ newFacilitatorId }` |
| `deck.changed` | tous | `{ deckSnapshot }` — après `deck.select` ou `round.prepare` (deck fourni). |
| `timer.changed` | tous | `{ enabled, seconds }` — après `timer.set` ou `round.prepare` (timer fourni). |
| `reveal.modeChanged` | tous | `{ anonymous }` — après `reveal.setMode` ou `round.prepare` (mode fourni). |
| `facilitator.presence` | tous | `{ present: boolean }` — présence du facilitateur (garde-fou §6.f). |
| `agenda.updated` | tous | `{ agenda }` (§8.1.a) — scénario courant, après toute action qui touche un round/item. |
| `error` | 1 client | `{ code, message, rejectedType, cid }` (§7) |

### 5.1 `state.sync` — le message le plus important

Envoyé à un seul client (au `join` initial, à la reconnexion, à l'arrivée d'un retardataire). **Ne rejoue pas l'historique** : donne l'état courant en un bloc.

```json
{
  "room": { "code": "K7RM4P", "title": "Sprint retro", "isTeam": false },
  "protocolVersion": 1,
  "roundState": "open",
  "subject": "Qui décide du budget outillage ?",
  "deckSnapshot": { "voteType": "delegation_poker", "cards": [ /* … calques + trad */ ] },
  "availableDecks": [ { "deckId": 3, "voteType": "delegation_poker", "cardBack": { /* … */ } } ],
  "participants": [
    { "participantId": "p-1", "username": "Sam", "role": "facilitator", "hasVoted": true },
    { "participantId": "p-2", "username": "Alex", "role": "voter", "hasVoted": false }
  ],
  "myRole": "voter",
  "myParticipantId": "p-2",
  "resultLayout": "cards",
  "myResponses": { "42": { "card": "consult" } },
  "result": null,
  "facilitatorPresent": true,
  "agenda": [ { "id": 12, "text": "Qui décide du budget outillage ?", "status": "current", "state": "open", "result": null, "everDecided": false, "items": [ { "id": 42, "text": "Qui décide du budget outillage ?", "sequence": 1 } ] } ],
  "items": [ { "id": 42, "text": "Qui décide du budget outillage ?", "sequence": 1 } ],
  "round": { "id": 12, "state": "open" },
  "deadline": null,
  "timer": { "enabled": false, "seconds": 10 },
  "reveal": { "anonymous": false, "canAnonymise": false }
}
```

Champ par champ (`realtime/services.py::build_state_sync`) :

- `myResponses` = **les réponses du seul client destinataire**, indexées par id d'item (les autres restent secrètes tant que `roundState` n'est ni `revealed` ni `acted`). L'ancienne clé `myVote` (le vote du premier item seul) est retirée en fin de 5b (§8.2.b) — voir §8.2.a.
- Si `roundState === "revealed"` **ou `"acted"`**, `state.sync` inclut aussi `itemResults` (§8.2.a) — un retardataire qui arrive après la révélation **voit les résultats** (le client traite `revealed` et `acted` comme un seul état d'affichage), et votera au tour suivant. Comme `vote.revealed`, il s'agit d'un décompte qui respecte l'anonymat : jamais de lien participant → carte sur un round anonyme.
- **Depuis 5a** (§8.1), `state.sync` porte aussi `items` — la liste des items du round courant, même forme que dans les faits `item.*` (`[{id, text, sequence}]`) — et `round` — `{id, state}` du round courant (`id: null` si aucun round actif). `subject` reste émis en doublon (le texte du premier item) : aucune date n'est fixée pour son retrait — c'est une clé de `state.sync`, distincte des anciennes intentions entrantes `subject.set`/`subject.add`/`subject.select` (§8.1.b), retirées en 5b.
- `room.isTeam` (`room.team_id is not None`) pilote le gating client de certaines options (le timer, notamment, est réservé aux salles d'équipe) ; le serveur reste de toute façon autoritaire côté validation.
- `myRole` et `myParticipantId` sont le rôle et l'identifiant **du destinataire**, renvoyés par le serveur — jamais déduits d'un état client persisté : une promotion facilitateur doit se voir immédiatement chez le facilitateur lui-même, pas seulement chez les autres.
- `resultLayout` fige la mise en page du dépouillement pour la salle (choisie par l'équipe à la création) : le client y adapte l'affichage dès la révélation.
- `availableDecks` liste le catalogue de decks jouables par cette salle (léger : pas les cartes), pour un sélecteur de deck côté facilitateur.
- `agenda` porte le scénario — chaque round de la salle, son état et, s'il a été acté, la valeur retenue et ses items. Chaque entrée porte `status` (`current`/`done`/`pending`, où on en est dans la séance) **et** `state` — le `RoundState` brut du round (`idle`/`open`/`revealed`/`acted`, même forme que le `round` de `state.sync`). Les deux coexistent parce qu'elles répondent à des questions différentes : un round ouvert puis abandonné pour un autre reste `status: "pending"` (rien n'a été acté) mais `state: "open"` — donc non retirable (`round.remove`, §8.4) — alors qu'un round jamais ouvert est aussi `status: "pending"` mais `state: "idle"`, lui retirable. `status` seul ne distingue pas ces deux cas. `everDecided` coexiste avec `result` et `state` parce qu'il répond à une troisième question, indépendante des deux premières : ce round a-t-il déjà porté un `Result`, une fois, n'importe quand — alors que `result` dit quelle valeur est retenue *là* (`null` aussi bien pour "jamais acté" que pour "acté puis réinitialisé" par `vote.reset`, qui vide les réponses mais laisse le `Result` en place). `everDecided` lève cette ambiguïté en restant `true` après un reset, parce qu'il teste exactement le même critère que la première garde de `remove_round` (§8.4) — sans cette clé, le front proposerait le retrait d'un round acté-puis-réinitialisé (`status: "pending"`, `state: "idle"`, `result: null`, en apparence un round jamais joué), et le serveur le refuserait.
- `deadline` est l'échéance ISO du round `open` courant (`null` sinon) ; `timer` porte le réglage courant de la salle (`enabled`, `seconds`).
- `reveal.anonymous` annonce le mode de révélation du round courant **avant que les votants ne répondent** ; `reveal.canAnonymise` dit si la salle (équipe payante) a le droit de basculer en anonyme.

---

## 6. Cas limites (règles figées)

| # | Situation | Règle |
|---|-----------|-------|
| a | **Secret des votes** | Aucune valeur avant `reveal`. `participation.update` ne porte que des IDs/compteurs. `myResponses` n'est renvoyé qu'à son propriétaire. **Après `reveal`, l'anonymat persiste** : le serveur n'émet qu'un décompte agrégé, jamais de couple participant → carte. Limite inhérente à connaître : avec un seul votant, `participation.update` (qui a voté) et le décompte (quelle carte) se recoupent — l'anonymat n'est atteignable qu'à partir de deux votants. |
| b | **Ordering / idempotence** | Le serveur **ignore** toute action incohérente avec l'état (ex. `response.cast` hors `open`). Re-voter la même carte = no-op ; voter une autre carte en `open` = remplacement. |
| c | **Révéler sans quorum** | Autorisé dès ≥ 1 vote. Un absent ne bloque pas la salle. |
| d | **Quitter avant révélation** | Le vote déjà émis **reste compté** (il fait partie du tour). `participant.left` diffusé, mais le vote persiste. |
| e | **Rejoindre en `revealed`** | Le retardataire reçoit un `state.sync` **incluant les résultats** ; il vote au tour suivant. |
| f | **Facilitateur déconnecté** | Après **~60 s** d'absence, le serveur passe `facilitatorPresent=false` et diffuse. Tout participant peut alors `facilitator.claim`. **Premier arrivé = nouveau facilitateur** : le serveur réassigne le rôle (`services.promote_facilitator`) et diffuse `facilitator.changed`. **Aucun nouveau token n'est émis** — l'autorité est portée par `rnd.facilitator_id`/`participant.role`, pas par le token, donc rien à réémettre (`services.promote_facilitator`, docstring). **Transfert définitif** : si le créateur d'origine revient, il redevient **votant** (le serveur ne refait plus confiance à l'ancien rôle pour le contrôle). |
| g | **Double-onglet (même token)** | Un seul participant ; la nouvelle connexion remplace l'ancienne. |

---

## 7. Erreurs

Réponse `error` (à l'émetteur seul), jamais un plantage silencieux :

```json
{ "code": "forbidden.not_facilitator", "message": "…", "rejectedType": "vote.open", "cid": "c-8f3a" }
```

Codes effectivement levés (`realtime/consumers.py`, `realtime/services.py`) : `protocol.version`,
`forbidden.not_facilitator`, `forbidden.subscription_required` (fonctionnalité d'équipe payante :
timer, révélation anonyme), `state.invalid_transition`, `token.unknown`, `guard.inactive` (claim
hors garde-fou). **`room.expired` n'existe pas** — prévu par ce document Phase 1 mais jamais
implémenté : une salle expirée ou inconnue résout en `token.unknown` (`resolve_participant`/
`room_by_code` renvoient `None` dans les deux cas, sans distinction de code pour l'appelant).

---

## 8. Transport & robustesse

- **Heartbeat** : le serveur répond `pong` à tout `ping` reçu (`realtime/consumers.py::receive_json`) — c'est un **écho**, pas un heartbeat piloté serveur : aucune tâche périodique n'émet de `ping`, et il n'existe pas de compteur « N pongs manqués » qui ferait considérer la connexion perdue. La présence (`participant.left`, `facilitatorPresent`) se met à jour sur le `disconnect()` ASGI réel (fermeture de socket détectée par Channels), pas sur un silence de heartbeat. `ping`/`pong` sert surtout de barrière de synchronisation dans les tests (aller-retour prouvant qu'un traitement précédent est terminé, cf. `CLAUDE.md` §Pièges).
- **Reconnexion** : le client retente avec backoff, rejoue `session.join` (token) → reçoit `state.sync`. **Restauration complète** (salle + vote + rôle).
- **Format** : JSON, enveloppe §2. Un `type` inconnu du serveur → `error` (`protocol.version` ou `state.invalid_transition`), jamais d'application partielle.

---

## 8.1 Items du round (5a)

> Ajouté 2026-09-11, livraison 5a (`docs/superpowers/plans/2026-09-11-5a-items-du-round.md`).
> Le round porte désormais **N items séquentiels** (`Round.items`, migrations 0010-0012),
> et non plus un sujet unique. Le WS gagne six nouvelles intentions et cinq nouveaux
> faits ; les anciens messages entrants `subject.set`/`subject.add`/`subject.select`
> ont d'abord été conservés comme **alias hérités**, le temps que
> `Facilitation_frontend` bascule sur `item.*`/`round.select`/`round.add` — puis
> **retirés en 5b** (2026-09-12), une relecture exhaustive du front déployé ayant
> confirmé la bascule effective — voir 8.1.b.

### 8.1.a Nouveaux événements

Entrants (facilitateur seul, comme les autres intentions de contrôle) :

| `type` | `payload` | Effet |
|--------|-----------|-------|
| `item.add` | `{ text }` | Ajoute un item au round **courant** (en crée un si aucun round actif). |
| `item.update` | `{ itemId, text }` | Réécrit le texte d'un item existant. |
| `item.remove` | `{ itemId }` | Retire un item du round courant. Refusé si l'item porte déjà un `Result` (ne réécrit pas l'historique). |
| `item.reorder` | `{ itemIds: [] }` | Refixe la séquence des items du round courant. Refusé si l'ensemble d'ids ne correspond pas exactement aux items existants. |
| `round.select` | `{ roundId }` | Reprend un round du scénario (le remet à `idle` s'il ne l'était pas). Ex-`subject.select`. |
| `round.add` | `{ text }` | Ouvre un round **de plus** dans la file (le scénario), sans le rendre courant sauf si aucun round n'existe encore. Ex-`subject.add`. **À ne pas confondre avec `item.add`** : celui-ci enrichit le round en cours, `round.add` avance dans la file. |

Sortants (tous) — **exhaustif**, y compris les faits déjà documentés en §5 quand une de
ces intentions les déclenche aussi :

| `type` | `payload` | Émis après |
|--------|-----------|------------|
| `item.added` | `{ roundId, items, itemId }` | `item.add` |
| `item.updated` | `{ roundId, items, itemId }` | `item.update` |
| `item.removed` | `{ roundId, items, itemId }` | `item.remove` |
| `item.reordered` | `{ roundId, items, itemId: null }` | `item.reorder` |
| `round.selected` | `{ roundId, items, text, nextState: "idle" }` | `round.select` |
| `agenda.updated` (§5) | `{ agenda }` | `item.add`, `item.update`, `item.remove`, `round.select`, `round.add` |
| `subject.updated` (§5) | `{ text }` | `item.add`, `item.update`, `round.select`, `round.add` |
| `vote.wasReset` (§5) | `{ nextState: "idle" }` | `round.select` |

`item.reorder` et `round.add` sont les deux intentions à ne déclencher **aucun** fait
propre : `item.reorder` émet uniquement `item.reordered` ; `round.add` réutilise
`agenda.updated`/`subject.updated`, suffisants pour refléter l'entrée ajoutée au
scénario.

Forme commune `{roundId, items, itemId}` : `roundId` est l'id du round courant (`null` s'il
n'y en a aucun), `items` la liste complète et à jour des items de ce round
(`[{id, text, sequence}]`, ordre du facilitateur), `itemId` l'item concerné par l'action
(`null` pour `item.reorder`, qui touche tous les items à la fois). `round.selected` porte en
plus `text` (le texte du premier item du round sélectionné, pour compatibilité avec les
clients qui n'affichent qu'un sujet) et `nextState`, toujours `"idle"`.

### 8.1.b Alias hérités — retirés en 5b

> Note datée 2026-09-11, mise à jour au retrait effectif en fin de 5b
> (2026-09-12) : `subject.set`, `subject.add`, `subject.select` (entrants)
> étaient des **alias hérités** vers `item.add`/`item.update`/`round.select`/
> `round.add`, conservés le temps que `Facilitation_frontend` bascule. Une
> relecture exhaustive du front déployé a confirmé la bascule effective — il
> n'émet plus que `item.add`/`item.update`/`round.add`/`round.select`/
> `round.prepare` — et les trois alias ont été **retirés du consumer et de
> `services`** (plus de branches `subject.set`/`subject.add`/`subject.select`
> dans `_dispatch`, `add_scenario_item` a perdu son paramètre `rejected_type`
> devenu sans objet). `round.add` est le seul point d'entrée pour ajouter une
> entrée au scénario, `round.select` le seul pour en reprendre une.
>
> `subject.updated` et `agenda.updated` (sortants) ne sont **pas** des alias à
> proprement parler : ce sont les faits que `item.add`/`item.update`/
> `round.select`/`round.add` diffusent eux-mêmes (voir la table 8.1.a). Une
> note antérieure les annonçait à tort comme voués à être retirés avec les
> entrants — corrigé ici : ils restent le contrat courant, et ce retrait ne
> les a pas touchés.

---

## 8.2 Réponses par item (5b)

> Ajouté 2026-09-11, livraison 5b (`.superpowers/sdd/2026-09-11-5b-responses/`). Le domaine
> écrit et agrège désormais une réponse **par item** (`Response.payload`, validé par le
> registre d'activités — `ActivitySpec.validate_value`), et non plus un vote unique par
> round. Le WS gagne une intention ouverte à tous les participants ; `vote.cast` et les
> clés plates de `vote.revealed` ont vécu en **alias hérités** le temps de la bascule
> front, et sont retirés en fin de 5b — voir 8.2.b.

### 8.2.a Nouvel événement

Entrant, **ouvert à tous les participants** (pas une intention de contrôle : pas de garde
facilitateur, contrairement à `item.*` en 8.1.a) :

| `type` | `payload` | Effet |
|--------|-----------|-------|
| `response.cast` | `{ itemId, payload }` | Enregistre/**remplace** la réponse de l'émetteur pour cet item (`payload` validé par le schéma que déclare le registre pour l'activité active). Autorisé **tant que le round est `open`**. Refusé si `itemId` n'appartient pas au round courant (`error` `state.invalid_transition`, `rejectedType: "response.cast"`). |

Sortant : `participation.update` (§5), diffusé après `response.cast` — la forme du fait ne
change pas (`{ voted, total, votedIds }`, jamais de valeur).

`vote.revealed` (§5) gagne une clé `itemResults` — **et non `items`**, déjà pris par la forme
`[{id, text, sequence}]` de `state.sync`/8.1 : fusionner les deux sous le même nom écraserait
silencieusement l'un des deux côté client. Forme (depuis le retrait des clés plates en fin
de 5b, §8.2.b, `itemResults` est la SEULE forme du décompte) :

```json
{
  "itemResults": [
    { "itemId": 42, "tally": [{ "cardValue": "5", "count": 2 }], "spread": { "min": 5, "max": 5 }, "anonymous": false, "votes": [ /* si nominatif */ ] }
  ],
  "anonymous": false,
  "reason": "timeout" | "facilitator"
}
```

Un bloc `itemResults[]` n'émet jamais `votes` sur un round anonyme — l'invariant §6.a tient
**par item**.

### 8.2.b Alias hérités — retirés en fin de 5b

> Note datée 2026-09-11, mise à jour en fin de 5b : `vote.cast` (entrant) et les clés
> plates `tally`/`spread`/`votes` de `vote.revealed` (sortant) ont vécu en **alias
> hérités** — `vote.cast {cardValue}` délégait à `response.cast` via la façade
> `services.cast_vote` (résolution du premier item du round courant), et les clés
> plates étaient recopiées du premier bloc de `itemResults`. Les deux ont vécu le temps
> que `Facilitation_frontend` bascule sur `response.cast`/`itemResults`/`myResponses` ;
> ce basculement est vérifié en production, et **la fenêtre de compatibilité est
> refermée** : `services.cast_vote` n'existe plus, `vote.cast` n'est plus un type
> reconnu par le consumer (`error` `state.invalid_transition`), et ni `vote.revealed`
> ni `state.sync` ne portent plus `tally`/`spread`/`votes` au premier niveau ni
> `myVote`. Ne pas les réintroduire — `itemResults`/`myResponses` sont la seule forme.

---

## 8.3 `round.configure` — config par round (5c)

> Ajoute 2026-09-12, livraison 5c (`.superpowers/sdd/2026-09-12-5c-type-par-round/`).
> Le registre d'activites (`realtime/activities.py::ActivitySpec.config_schema`)
> sait desormais valider une config par round ; `round.configure` ouvre ce
> reglage sur le contrat WS, separement d'un `round.prepare` complet — utile
> pour ne toucher qu'au deck, ou qu'a la config, sans repasser par le sujet.

Entrant (facilitateur seul, comme les autres intentions de controle) :

| `type` | `payload` | Effet |
|--------|-----------|-------|
| `round.configure` | `{ roundId, deckId?, config? }` | Fige la config (et, en option, le deck) d'un round encore `idle`. `deckId` et `config` sont tous deux optionnels : configurer sans changer de deck fonctionne, changer de deck sans configuration aussi. `config` est valide contre le `config_schema` de l'activite du deck en jeu (`realtime/activities.py::validate_config`) ; une cle inconnue ou un type errone est refuse. Refuse si le round n'existe pas ou n'est plus `idle` (`error` `state.invalid_transition`, `rejectedType: "round.configure"`). |

Sortant (tous) :

| `type` | `payload` | Emis apres |
|--------|-----------|------------|
| `round.configured` | `{ roundId, deckSnapshot, config }` | `round.configure`, toujours. |
| `deck.changed` (§5) | `{ deckSnapshot }` | `round.configure`, **seulement si `deckId` a ete fourni** — meme fait que celui deja diffuse par `deck.select`/`round.prepare` (§5), pour que les clients actuels n'aient pas de nouveau gestionnaire a ecrire pour suivre un changement de deck. |

`round.configure` ne touche pas au sujet ni aux items : contrairement a `round.prepare`,
il ne cree ni ne selectionne aucun round — `roundId` doit deja exister et etre `idle`.

---

## 8.4 `round.reorder` / `round.remove` — le scenario (5d)

> Ajoute 2026-09-12, livraison 5d (`.superpowers/sdd/2026-09-12-5d-scenario-prepare/`).
> `Round.sequence` (tache 1) donne a la file de rounds un ordre explicite ; ces
> deux intentions sont les gestes qui en font un scenario compose en amont --
> reordonner et elaguer. `realtime/services.py::reorder_rounds`/`remove_round`
> portent la logique de domaine (tache 2) ; ce paragraphe ne couvre que leur
> cablage sur le contrat WS (tache 3).

Entrant (facilitateur seul, comme les autres intentions de controle) :

| `type` | `payload` | Effet |
|--------|-----------|-------|
| `round.reorder` | `{ roundIds: [] }` | Refixe `Round.sequence` sur l'ordre donne. Refuse si l'ensemble d'ids ne correspond pas exactement aux rounds existants de la salle (`error` `state.invalid_transition`, `rejectedType: "round.reorder"`). Deplacer un round deja `ACTED` est autorise : la sequence ne pilote que l'affichage de l'agenda, jamais l'historique (`history/`, trie sur `decided_at`). |
| `round.remove` | `{ roundId }` | Retire un round du scenario. Refuse (meme `error`, `rejectedType: "round.remove"`) si le round n'existe pas, s'il porte deja un `Result`, s'il n'est pas `idle`, ou s'il est le round courant de la salle — voir `realtime/services.py::remove_round` pour le detail des trois gardes. Le facilitateur doit d'abord designer un autre round courant via `round.select` avant de pouvoir retirer l'ancien. |

Sortant (tous) :

| `type` | `payload` | Emis apres |
|--------|-----------|------------|
| `agenda.updated` (§5) | `{ agenda }` | `round.reorder`, `round.remove` |

`round.reorder` et `round.remove` sont deux intentions de plus a ne declencher
**aucun** fait propre : l'agenda rediffuse porte deja l'id du round courant
(`status: "current"`), que ni l'une ni l'autre ne peut jamais changer --
`round.remove` le refuse explicitement (troisieme garde ci-dessus) et
`round.reorder` ne touche qu'a `Round.sequence`. Verifie par lecture de
`room-socket.service.ts::applyEvent` (cas `agenda.updated`) : le front en tire
deja `currentRoundId` de l'entree marquee `'current'`, sans lecteur dedie a
ajouter.

---

## 8.5 `round.bind` / `round.resolve` — chaînage (5e)

> Ajouté 2026-09-12, livraison 5e (`.superpowers/sdd/2026-09-12-5e-chainage/`).
> Le domaine (`realtime/services.py::bind_round`/`chaining_candidates`/
> `resolve_source`, tâches 1-2) sait déjà déclarer une liaison de chaînage
> entre deux rounds et la résoudre en copie (design 2026-09-11 §7) ; ce
> paragraphe ouvre ce comportement sur le contrat WS (tâche 3). Vocabulaire
> des clés, désambiguïsé et à ne pas re-mélanger : `sourceItemId` désigne un
> item du round SOURCE, `itemId` un item du round qu'on regarde, `originItemId`
> la racine de la chaîne (voir task-2-report.md).

Entrant (facilitateur seul, comme les autres intentions de contrôle) :

| `type` | `payload` | Effet |
|--------|-----------|-------|
| `round.bind` | `{ roundId, sourceRoundId, rule }` | Déclare que `roundId` (la cible) reprendra de `sourceRoundId` (la source) ce que dit `rule` (`{take: "items"\|"results", mode: "auto"\|"manual", top: int\|null}`). Ne copie rien — voir `round.resolve` pour le mode manuel, et §8.5.a pour le mode auto. Refusé (`error` `state.invalid_transition`, `rejectedType: "round.bind"`) si l'un des deux rounds est inconnu, si `roundId == sourceRoundId`, si la règle est malformée, si la cible ne consomme pas d'items, si `take: "results"` mais la source ne produit pas de résultats, ou si `top` est posé sans classement disponible ou sans `take: "results"` — voir `realtime/services.py::bind_round` pour le détail des gardes. |
| `round.resolve` | `{ roundId, sourceItemIds? }` | Valide une sélection manuelle : copie dans `roundId` exactement les items de `sourceItemIds` (des `sourceItemId` pris dans `round.candidates`, §8.5.a). Ignoré en mode `auto` (déjà résolu au démarrage, §8.5.a) ; exigé en mode `manual` (refusé sinon). Idempotent : une liaison déjà résolue renvoie sa copie existante sans la rejouer. Refusé (même `error`, `rejectedType: "round.resolve"`) si le round n'a pas de source liée, ou si `sourceItemIds` contient un candidat inconnu — voir `realtime/services.py::resolve_source`. |

Sortant (tous, sauf `round.candidates`) :

| `type` | `payload` | Émis après |
|--------|-----------|------------|
| `round.bound` | `{ roundId, sourceRoundId, rule }` | `round.bind`, toujours — aucun autre fait n'est nécessaire, la liaison ne change ni les items ni l'état d'aucun round. |
| `round.resolved` | `{ roundId, items: [{itemId, text, sequence, originItemId, sourceItemId, authorId}] }` | `round.resolve`, toujours — même forme que `_chained_items_payload` (`realtime/services.py`). |
| `agenda.updated` (§5) | `{ agenda }` | `round.resolve` (les items du round consommateur viennent de changer). |
| `subject.updated` (§5) | `{ text }` | `round.resolve` (le premier item peut avoir changé). |

### 8.5.a Mode auto et candidats du mode manuel

En mode `auto`, `round.select` (§8.1.a) résout la liaison tout seul au moment
où le round lié devient courant (`realtime/services.py::select_round`, appel
automatique à `resolve_source`) — `round.selected` porte déjà les items
résultants, aucun fait de plus n'est donc nécessaire ici.

En mode `manual`, il n'y a **aucun message client pour demander les
candidats**. Quand un round lié en mode `manual`, pas encore résolu, devient
courant via `round.select`, le serveur émet lui-même :

| `type` | Cible | `payload` | Émis après |
|--------|-------|-----------|------------|
| `round.candidates` | **1 client, le facilitateur** | `{ roundId, candidates: [{sourceItemId, text, authorId}] }` | `round.select`, quand le round devenu courant est lié en mode `manual` et pas encore résolu. |

**Réservé au facilitateur, filtré à l'émission** — jamais une diffusion de
groupe suivie d'un masquage côté client. `round.select` exige déjà le
facilitateur (`_require_facilitator`), donc la connexion qui vient de
l'émettre EST la sienne : ce fait lui est renvoyé directement (`self._emit`),
sans passer par `self.channel_layer`, exactement comme `state.sync` (§5.1) ne
part jamais qu'au client qui a demandé le join.

⚠️ Écart connu : `state.sync` ne porte pas encore ces candidats à la
reconnexion (`realtime/services.py::build_state_sync` n'a pas été touché par
cette livraison, hors périmètre de la tâche 3) — un facilitateur qui recharge
sur un round manuel non résolu doit encore re-sélectionner le round pour les
revoir. À traiter séparément.

---

## 9. Hors périmètre (Phase 1)

- ~~❌ `facilitator.transfer` **volontaire** (Phase 2)~~ — **implémenté** : l'intention WS
  `facilitator.transfer {targetParticipantId}` (facilitateur seul) existe et fonctionne
  (`realtime/services.py::transfer_facilitator`, voir §4) ; ce n'est plus hors périmètre.
- ❌ Comptes/auth sur le socket (identité = token éphémère).
- ❌ Événements de board / historique / présence persistée (Phase 2).
- ❌ Chiffrement applicatif des payloads (au-delà de WSS/TLS).

---

## 10. Suite

1. **Modèle de données détaillé** (états de session, snapshot, `TextLayer` + traductions parler, `Result`).
2. **Plan d'implémentation** task-by-task (format `docs/superpowers/plans/`), consommant ce contrat.
