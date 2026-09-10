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
| 2 | **Autorité facilitateur** | Les événements de contrôle (`vote.open/reveal/reset`, `result.act`, `subject.set`) ne sont acceptés **que** du facilitateur. Le serveur **rejette** sinon (le masquage front n'est qu'un confort). |
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
{ "v": 1, "type": "vote.cast", "payload": { }, "cid": "c-8f3a", "ts": 1720353600 }
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
| `subject.set` | facilitateur | `{ text }` | Définit/édite le sujet courant (état `idle`). |
| `vote.open` | facilitateur | `{ }` | Ouvre le tour (`idle → open`). Refusé si pas de sujet. |
| `vote.cast` | votant *(et facilitateur s'il vote)* | `{ cardValue }` | Enregistre/**remplace** le vote de l'émetteur. Autorisé **tant que `open`**. Idempotent (même valeur = no-op). |
| `vote.reveal` | facilitateur | `{ }` | `open → revealed`. Autorisé dès **≥ 1 vote** (pas de quorum). |
| `result.act` | facilitateur | `{ chosenValue }` | `revealed → acted`. Fige le résultat retenu (défaut proposé = mode/médiane, modifiable). |
| `vote.reset` | facilitateur | `{ }` | Efface les votes du tour → `idle` (si nouveau sujet à saisir) ou `open`. |
| `facilitator.claim` | tout participant présent | `{ }` | **Uniquement** si le garde-fou est actif (§6.f). Premier arrivé = nouveau facilitateur. |

Toute intention **incohérente avec l'état courant** (ex. `vote.cast` en `revealed`) est **rejetée** par `error`, pas appliquée (§6.b).

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
| `vote.revealed` | tous | `{ tally: [{ cardValue, count }], spread: { min, max } }` — **révélation anonyme** : décompte par valeur, aucun lien participant → carte. Seules les valeurs ayant ≥ 1 voix figurent, dans l'ordre du deck. Porte aussi `reason: "timeout" \| "facilitator"`. |
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
  "myVote": "consult",
  "result": null,
  "facilitatorPresent": true
}
```

- `myVote` = **le vote du client destinataire uniquement** (les autres restent secrets tant que `roundState !== "revealed"`).
- Si `roundState === "revealed"`, `state.sync` inclut aussi le `tally` (un retardataire qui arrive en `revealed` **voit les résultats**, et votera au tour suivant). Comme `vote.revealed`, il s'agit d'un décompte anonyme : jamais de lien participant → carte.

---

## 6. Cas limites (règles figées)

| # | Situation | Règle |
|---|-----------|-------|
| a | **Secret des votes** | Aucune valeur avant `reveal`. `participation.update` ne porte que des IDs/compteurs. `myVote` n'est renvoyé qu'à son propriétaire. **Après `reveal`, l'anonymat persiste** : le serveur n'émet qu'un décompte agrégé, jamais de couple participant → carte. Limite inhérente à connaître : avec un seul votant, `participation.update` (qui a voté) et le décompte (quelle carte) se recoupent — l'anonymat n'est atteignable qu'à partir de deux votants. |
| b | **Ordering / idempotence** | Le serveur **ignore** toute action incohérente avec l'état (ex. `vote.cast` hors `open`). Re-voter la même carte = no-op ; voter une autre carte en `open` = remplacement. |
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

## 9. Hors périmètre (Phase 1)

- ❌ `facilitator.transfer` **volontaire** (Phase 2) — seul le garde-fou §6.f réassigne en Phase 1.
- ❌ Comptes/auth sur le socket (identité = token éphémère).
- ❌ Événements de board / historique / présence persistée (Phase 2).
- ❌ Chiffrement applicatif des payloads (au-delà de WSS/TLS).

---

## 10. Suite

1. **Modèle de données détaillé** (états de session, snapshot, `TextLayer` + traductions parler, `Result`).
2. **Plan d'implémentation** task-by-task (format `docs/superpowers/plans/`), consommant ce contrat.
