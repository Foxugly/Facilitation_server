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
| 2 | **Autorité facilitateur** | Les événements de contrôle (`vote.open/reveal/reset`, `result.act`, `item.*`, `round.*`) ne sont acceptés **que** du facilitateur. Le serveur **rejette** sinon (le masquage front n'est qu'un confort). |
| 3 | **Rôle porté par le token, pas par la connexion** | À la reconnexion, token → participant → rôle + vote restaurés. Une coupure ne perd pas le rôle. |
| 4 | **Secret réel des votes** | Aucune valeur de vote n'est diffusée avant `reveal`. Avant : seulement « a voté / pas voté ». |
| 5 | **HTTP crée/résout la salle ; WS gère la vie dans la salle** | Le socket ne s'ouvre qu'une fois *dans* la salle. |

---

## 1. Frontière HTTP ↔ WebSocket

**HTTP (REST, convention flotte)** — avant d'ouvrir le socket :

| Méthode | Route | Corps | Retour |
|---------|-------|-------|--------|
| `POST` | `/api/rooms` | `{ title?, username }` | `{ code, participantToken, role: "facilitator", deckSnapshot, roomTitle }` |
| `POST` | `/api/rooms/{code}/join` | `{ username }` | `{ code, roomTitle, participantToken, role: "voter", deckSnapshot }` — **404** si salle inconnue/expirée |
| `GET` | `/api/rooms/{code}` | — | Résout l'existence d'une salle (arrivée par URL) : `{ code, roomTitle, exists }` |

- Le **`participantToken`** est un **secret aléatoire** généré serveur (long, non devinable). Le client le stocke en `localStorage` **à côté du username** et le rejoue à chaque (re)connexion WS.
- Le **rôle vit côté serveur** (table `token → rôle` dans l'état de salle). Le client **ne s'auto-déclare jamais** facilitateur ; il n'envoie que son token.
- Le **`deckSnapshot`** est immuable pour la durée de la salle (voir scope §3.6).

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

- **Deux rôles en Phase 1** : `facilitator` (= le **créateur**, un seul rôle de contrôle) et `voter`. Le transfert *volontaire* de rôle est Phase 2 ; seul le **garde-fou** (§6.f) réassigne en Phase 1.
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
| `vote.reset` | facilitateur | `{ }` | Efface les votes du tour → `idle` (si nouveau sujet à saisir) ou `open`. |
| `facilitator.claim` | tout participant présent | `{ }` | **Uniquement** si le garde-fou est actif (§6.f). Premier arrivé = nouveau facilitateur. |

> Cette table date de la Phase 1 (2026-07-07) et décrivait aussi `subject.set` et
> `vote.cast`. Les deux sont remplacés — le premier par `item.add`/`item.update`/
> `round.select` (§8.1.a), le second par `response.cast` (§8.2.a) — et **retirés** :
> `Facilitation_frontend` n'en a plus besoin, vérifié en prod (§8.1.b, §8.2.b).

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
| `vote.wasReset` | tous | `{ nextState: "idle" \| "open" }` |
| `facilitator.changed` | tous | `{ newFacilitatorId }` |
| `error` | 1 client | `{ code, message, rejectedType, cid }` (§7) |

### 5.1 `state.sync` — le message le plus important

Envoyé à un seul client (au `join` initial, à la reconnexion, à l'arrivée d'un retardataire). **Ne rejoue pas l'historique** : donne l'état courant en un bloc.

```json
{
  "room": { "code": "K7RM4P", "title": "Sprint retro" },
  "protocolVersion": 1,
  "roundState": "open",
  "subject": "Qui décide du budget outillage ?",
  "deckSnapshot": { "voteType": "delegation_poker", "cards": [ /* … calques + trad */ ] },
  "participants": [
    { "participantId": "p-1", "username": "Sam", "role": "facilitator", "hasVoted": true },
    { "participantId": "p-2", "username": "Alex", "role": "voter", "hasVoted": false }
  ],
  "myResponses": { "42": { "card": "consult" } },
  "result": null,
  "facilitatorPresent": true
}
```

- `myResponses` = **les réponses du seul client destinataire**, indexées par id d'item (les autres restent secrètes tant que `roundState !== "revealed"`). L'ancienne clé `myVote` (le vote du premier item seul) est retirée en fin de 5b (§8.2.b) — voir §8.2.a.
- Si `roundState === "revealed"`, `state.sync` inclut aussi `itemResults` (§8.2.a) — un retardataire qui arrive en `revealed` **voit les résultats**, et votera au tour suivant. Comme `vote.revealed`, il s'agit d'un décompte qui respecte l'anonymat : jamais de lien participant → carte sur un round anonyme.
- **Depuis 5a** (§8.1), `state.sync` porte aussi `items` — la liste des items du round courant, même forme que dans les faits `item.*` (`[{id, text, sequence}]`) — et `round` — `{id, state}` du round courant (`id: null` si aucun round actif). `subject` reste émis en doublon (le texte du premier item) : aucune date n'est fixée pour son retrait — c'est une clé de `state.sync`, distincte des anciennes intentions entrantes `subject.set`/`subject.add`/`subject.select` (§8.1.b), retirées en 5b.

---

## 6. Cas limites (règles figées)

| # | Situation | Règle |
|---|-----------|-------|
| a | **Secret des votes** | Aucune valeur avant `reveal`. `participation.update` ne porte que des IDs/compteurs. `myResponses` n'est renvoyé qu'à son propriétaire. **Après `reveal`, l'anonymat persiste** : le serveur n'émet qu'un décompte agrégé, jamais de couple participant → carte. Limite inhérente à connaître : avec un seul votant, `participation.update` (qui a voté) et le décompte (quelle carte) se recoupent — l'anonymat n'est atteignable qu'à partir de deux votants. |
| b | **Ordering / idempotence** | Le serveur **ignore** toute action incohérente avec l'état (ex. `response.cast` hors `open`). Re-voter la même carte = no-op ; voter une autre carte en `open` = remplacement. |
| c | **Révéler sans quorum** | Autorisé dès ≥ 1 vote. Un absent ne bloque pas la salle. |
| d | **Quitter avant révélation** | Le vote déjà émis **reste compté** (il fait partie du tour). `participant.left` diffusé, mais le vote persiste. |
| e | **Rejoindre en `revealed`** | Le retardataire reçoit un `state.sync` **incluant les résultats** ; il vote au tour suivant. |
| f | **Facilitateur déconnecté** | Après **~60 s** d'absence, le serveur passe `facilitatorPresent=false` et diffuse. Tout participant peut alors `facilitator.claim`. **Premier arrivé = nouveau facilitateur** : le serveur réassigne le rôle, **émet un nouveau token facilitateur** au claimeur, diffuse `facilitator.changed`. **Transfert définitif** : si le créateur d'origine revient, il redevient **votant** (le serveur ne refait plus confiance à l'ancien token pour le contrôle). |
| g | **Double-onglet (même token)** | Un seul participant ; la nouvelle connexion remplace l'ancienne. |

---

## 7. Erreurs

Réponse `error` (à l'émetteur seul), jamais un plantage silencieux :

```json
{ "code": "forbidden.not_facilitator", "message": "…", "rejectedType": "vote.open", "cid": "c-8f3a" }
```

Codes attendus (liste extensible) : `protocol.version`, `forbidden.not_facilitator`,
`state.invalid_transition`, `room.expired`, `token.unknown`, `guard.inactive` (claim hors garde-fou).

---

## 8. Transport & robustesse

- **Heartbeat** : `ping`/`pong` applicatif toutes les ~20 s (Channels ne détecte pas seul une connexion morte). Après **N pongs manqués**, le serveur considère la connexion perdue → présence à jour, garde-fou éventuel.
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
`agenda.updated`/`subject.updated`, exactement ce que diffusait l'alias `subject.add`.

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

## 9. Hors périmètre (Phase 1)

- ❌ `facilitator.transfer` **volontaire** (Phase 2) — seul le garde-fou §6.f réassigne en Phase 1.
- ❌ Comptes/auth sur le socket (identité = token éphémère).
- ❌ Événements de board / historique / présence persistée (Phase 2).
- ❌ Chiffrement applicatif des payloads (au-delà de WSS/TLS).

---

## 10. Suite

1. **Modèle de données détaillé** (états de session, snapshot, `TextLayer` + traductions parler, `Result`).
2. **Plan d'implémentation** task-by-task (format `docs/superpowers/plans/`), consommant ce contrat.
