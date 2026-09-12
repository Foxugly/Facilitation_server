# Scénario préparé, N items par round, chaînage — design

**Date :** 2026-09-11
**Étape du plan :** 5 (« N items par round »), élargie en programme 5a → 5e.
**Statut :** design validé, non implémenté.

Ce document remplace la ligne « 5. N items par round » du plan de `CLAUDE.md` :
les réponses de conception ci-dessous en ont fait un programme de cinq livraisons.

---

## 1. Ce qu'on construit

Le produit visé n'est pas « du poker à plusieurs sujets » mais un **scénario
d'atelier préparé en amont puis joué** (modèle Wooclap) :

> Le facilitateur compose, avant la séance, une suite d'activités — Brainstorming,
> puis Dot Voting **sur les items brainstormés**, puis SWOT sur ces mêmes idées.
> Pendant la séance il déroule cette suite ; chaque activité récupère ce que la
> précédente a produit.

Trois conséquences structurent tout le reste :

1. **Les items appartiennent au round**, pas à la room. Un round est une activité
   jouée sur N items. Un item est aussi bien un sujet posé par le facilitateur
   qu'un **post-it écrit par un participant** : c'est le type d'activité qui dit
   qui a le droit d'en créer.
2. **Le type d'activité se choisit par round**, pas par room. Le scénario mélange
   les activités dans n'importe quel ordre.
3. **Le chaînage est une liaison déclarée à la préparation, résolue en copie au
   démarrage** du round consommateur — la sortie de la source n'existe pas encore
   quand on prépare.

## 2. Décisions actées

| Question | Décision |
|---|---|
| Portée | Schéma **et** contrat WS. Livraison couplée back + front. |
| Scénario | File de rounds préparés portés par la room, chacun avec ses items. `Room.subjects` disparaît. |
| Type d'activité | Par round, porté par `Round.deck_snapshot` (`voteType` + `resolutionStrategy`) et `Round.config` — **pas** de FK `Round.vote_type` (décision revue en 5c, voir §3). `Room.vote_type` n'est plus que le défaut à la création. |
| Chaînage | Liaison déclarée (`Round.source_round` + règle), **copie** des items au démarrage, `Item.origin_item` pour la traçabilité. Jamais une référence vivante. |
| Création d'items | Déclarée par le registre : facilitateur seul (poker) ou tous les participants (brainstorming). `Item.author` porte l'auteur. |
| Subset du chaînage | **Mode choisi par liaison** : automatique (tout / top N) ou manuel (le facilitateur coche au démarrage du round consommateur). |
| Stratégie | Strangler additif en cinq livraisons, chacune verte et déployable, le poker fonctionnant à chaque étape. |

Approches écartées : la bascule en une seule migration (diff énorme, rien de
vérifiable avant la fin) et le maintien de deux chemins permanents (poker legacy
d'un côté, activités neuves de l'autre — dette définitive, et contradiction
frontale avec « ajouter une activité ne doit toucher que le registre »).

## 3. Modèle cible

```
Room ──< Round (séquentiels, le scénario) ──< Item ──< Response
                   │                                      │
                   └──< Result (un par item acté) ─────────┘
```

**`Round`** — gagne :

- `sequence` (PositiveSmallInteger) : la place dans le scénario.
- ~~`vote_type` (FK `decks.VoteType`)~~ — **décision révisée en 5c : ce champ
  n'existe pas et n'existera pas.** Le type d'un round, c'est celui de son
  `deck_snapshot` (`voteType` + `resolutionStrategy`), déjà figé sur le round
  depuis la préparation. Une FK `vote_type` aurait dupliqué cette information
  dans les tables `decks`, que la couche temps réel n'a **pas le droit de
  lire** (règle d'immuabilité du dépôt : elle ne lit que les blobs figés).
  Deux sources de vérité finissent par diverger — un round dont le `vote_type`
  dirait une chose et le `deck_snapshot` une autre serait un bug qu'aucun test
  n'attraperait. Coût si cette décision se révèle fausse : si une activité
  devait un jour exister sans deck, elle n'aurait aucun porteur de type — à
  rouvrir à ce moment-là, pas avant.
- `config` (JSON, défaut `{}`) : la configuration de l'activité, validée par le
  schéma que déclare le registre.
- **Appris en 5c, non anticipé par ce design :** un round garde son type (son
  `deck_snapshot`) **pour toute sa vie**. `vote.reset` ne l'efface plus, et
  rejouer un round acté (`_replay_round`) le recopie sur le round neuf, avec
  `config` et les items. Sans cette règle, un round dont le deck actif de la
  room aurait changé de type entretemps (poker → dot voting, ou l'inverse)
  reviendrait rejoué dans un autre type que celui qui a produit ses items et
  son `Result` d'origine.
- `source_round` (FK self, null) et `source_rule` (JSON, null) : la liaison de
  chaînage.
- perd `subject` (FK unique) au profit du `related_name` `items`.

**`Item`** — ex-`Subject`, rattaché au **round** :

- `round` (FK, CASCADE), `text` (300), `sequence`, `created_at`.
- `origin_item` (FK self, null, SET_NULL) : l'item dont celui-ci est la copie.
- `author` (FK `Participant`, null, SET_NULL) : qui l'a écrit. **Toujours
  renseigné** quand un participant crée le post-it ; null quand c'est le
  facilitateur qui pose un sujet au nom de la room. L'anonymat d'un
  brainstorming est, comme pour `Response`, une **politique d'affichage
  appliquée côté serveur** — le serveur n'émet pas la clé `author`, il ne la
  vide pas en base.

**`Response`** — ex-`Vote` :

- `round`, `participant`, **`item`** (FK), `payload` (JSON), horodatages.
- Contrainte d'unicité `(item, participant)` — et non plus `(round, participant)` :
  un participant répond à chaque item du round.
- `card_value` disparaît : le poker écrit `{"card": "<value>"}`.

**`Result`** — figé au reveal, jamais recalculé (règle existante, inchangée) :

- `round` + `item` (au lieu de `round` OneToOne + `subject`), unique `(round, item)`.
- `chosen_value` reste tel quel tant que la seule activité qui acte est le poker ;
  il deviendra un `payload` JSON quand une activité produira autre chose qu'une
  valeur de carte. **Hors périmètre de ce programme.**

### Ce que le modèle ne fait pas

- Pas de table par activité. Une seule `Response`, son `payload` validé par le
  schéma déclaré par le type. C'est la règle P-cible de `CLAUDE.md`, réaffirmée.
- Pas de nouvel état de cycle. `DRAFT`, `READY`, `CLOSED`, `COMPLETED` restent
  hors périmètre : `idle` tient le rôle de READY (un round préparé est un round
  `idle` qui n'est pas `Room.current_round`). Introduire CLOSED est une étape à
  part, pour les activités qui ne révèlent rien.
- Pas d'anonymat côté client : inchangé, le serveur n'émet simplement pas la clé.

## 4. Migration de données

Le point délicat est que `Subject` appartient à la room et qu'un subject peut
avoir **zéro ou plusieurs** rounds (`add_subject` ne crée un round que s'il n'y en
a pas de courant ; `select_subject` en crée un à la demande).

| Cas | Traitement |
|---|---|
| Subject avec 1 round | L'item devient l'unique item de ce round. |
| Subject avec N rounds (re-vote) | **Une copie d'item par round**, `origin_item` pointant sur la première. L'historique d'un round passé ne doit pas bouger si le texte est reformulé plus tard. |
| Subject orphelin (préparé, jamais joué) | Création d'un `Round` `idle` qui le porte, à sa place dans la séquence. C'est exactement le scénario préparé. |

`Round.sequence` est attribué dans l'ordre `Subject.sequence`, puis
`Round.created_at` pour les re-votes. `Result.subject` → `Result.item` suit
l'item du round du résultat.

**Vérifier la migration sur PostgreSQL** avant tout push (piège connu du dépôt :
sqlite laisse passer des violations NOT NULL / unique).

## 5. Contrat WS

Le contrat (`docs/superpowers/specs/delegation-poker-realtime-contract.md`) est
**étendu, pas doublé**. `session.join` reste tel quel (exception assumée au ban du
mot « session »).

### Entrants

**Qui a le droit d'émettre `item.*` n'est pas fixé par le contrat mais par le
registre** (`items_authored_by`). Pour le poker c'est le facilitateur seul, via
`_require_facilitator` comme aujourd'hui ; pour un brainstorming, tout
participant, et il ne peut alors modifier ou supprimer que **ses propres** items
— le facilitateur, lui, peut toujours éditer et retirer n'importe lequel. Les
autres messages restent facilitateur.

| Message | Remplace | Livraison |
|---|---|---|
| `item.add {roundId, text}` | `subject.add` | 5a |
| `item.update {itemId, text}` | `subject.set` | 5a |
| `item.remove {itemId}` | — | 5a |
| `item.reorder {roundId, itemIds[]}` | — | 5a |
| `round.select {roundId}` | `subject.select` | 5a |
| `response.cast {itemId, payload}` *(tous)* | `vote.cast {cardValue}` | 5b |
| `round.configure {roundId, voteType, config}` | — | 5c |
| `scenario.add / remove / reorder` | — | 5d |
| `round.bind {roundId, sourceRoundId, rule}` | — | 5e |

### Sortants

`item.added / updated / removed / reordered`, `round.selected`, `scenario.updated`,
et `round.revealed` portant un décompte **par item**.

### Compatibilité

Les anciens types (`subject.*`, `vote.cast`, `vote.revealed`, `agenda.updated`)
restent acceptés **pendant 5a et 5b uniquement**, en alias traduits vers les
nouveaux. `state.sync` porte simultanément `subject` (déprécié) et `items[]` le
temps que le front bascule. Les alias sont supprimés à la fin de 5b : c'est une
dette datée, pas un mode de compatibilité permanent.

`state.sync` gagne :

```jsonc
"round":    { "id": 12, "voteType": "delegation_poker", "state": "open", "sequence": 2 },
"items":    [ { "id": 34, "text": "Budget ?", "sequence": 1 } ],
"myResponses": { "34": { "card": "4" } },   // ex-myVote
"scenario": [ { "id": 12, "voteType": "…", "status": "current|done|pending", "itemCount": 3 } ]
```

`agenda` (liste de subjects) devient `scenario` (file de rounds) : même rôle,
autre granularité. Le renommage a lieu en **5d**, quand la file de rounds devient
réellement manipulable ; en 5a et 5b, `agenda` reste émis, alimenté par les items
du round courant.

## 6. Registre d'activités

`realtime/activities.py` — `ActivitySpec` gagne cinq champs, et reste la
**seule** chose à toucher pour ajouter une activité :

- `config_schema` / `payload_schema` : validation de `Round.config` et de
  `Response.payload`.
- `aggregate(responses, items) -> results` : l'agrégateur, aujourd'hui dispersé
  dans `services.revealed_payload`.
- `consumes` / `produces` (`items` / `results` / `none`) : ce que le chaînage a le
  droit de brancher sur quoi.
- `items_authored_by` (`facilitator` | `participants`) : qui crée les items. C'est
  ce champ, et non une condition en dur dans le consumer, qui autorise
  `item.add` — sans quoi ajouter le brainstorming toucherait `consumers.py`. Le registre front déclare déjà ces deux champs, avec
  `consumes: 'subjects'` — à renommer en `'items'` dans la livraison 5a, pour que
  les deux registres parlent le même vocabulaire.

La clef reste `VoteType.resolution_strategy` côté serveur (deux decks peuvent
partager une résolution) et `VoteType.code` côté front (le rendu, lui, est
propre au type). Cette asymétrie est voulue ; la documenter dans les deux
fichiers.

## 7. Chaînage

À la préparation, un round déclare sa source et **le mode de sélection** :

```
Round.source_round = <round amont>
Round.source_rule  = {
  "take": "items" | "results",       // ce qu'on reprend de la source
  "mode": "auto" | "manual",         // comment le subset se décide
  "top": 3 | null                    // mode auto : tout (null) ou les N premiers
}
```

Un round sans `source_round` part de zéro : items saisis à la main, ou post-its
écrits par les participants. « On ne reprend rien » est donc l'absence de
liaison, pas une règle particulière.

**Mode `auto`** — au moment où le round devient courant, le serveur copie tout
(ou les `top` premiers selon le classement produit par l'agrégateur de la
source). Aucune intervention.

**Mode `manual`** — le serveur présente au facilitateur les candidats de la
source ; il coche ce qui passe, et la copie n'a lieu qu'à sa validation. C'est le
mode qui correspond à l'atelier réel : on ne sait pas d'avance ce qu'un
brainstorming produira, et un post-it hors-sujet doit pouvoir être écarté.

Dans les deux cas la copie est identique : chaque élément retenu devient un
`Item` du round consommateur, `origin_item` renseigné, et `author` recopié quand
la source est un jeu de post-its — l'auteur d'une idée ne se perd pas en passant
à l'étape suivante. Le facilitateur peut ensuite éditer, réordonner ou supprimer
ces items avant d'ouvrir : c'est une copie, la source n'en sait rien.

- « Send to Dot Voting » à chaud = une liaison `manual` créée pendant la séance
  plutôt que déclarée d'avance. Un seul chemin de code.
- Source encore ouverte : autorisée (copie de l'état à l'instant T).
- Ré-ouvrir la source **n'actualise pas** les copies déjà faites ; l'UI doit le
  signaler. Décision reprise telle quelle de `CLAUDE.md`.
- Le registre refuse une liaison dont le `produces` de la source ne correspond pas
  au `consumes` de la cible, et refuse `top` sur une source dont l'agrégateur ne
  produit aucun classement.

## 8. Découpage

Chaque livraison : une PR, `pytest` vert (référence actuelle **240 passed**), le
poker jouable de bout en bout, et les e2e front verts quand le contrat bouge.

| # | Livraison | Contenu | Vérification |
|---|---|---|---|
| **5a** ✅ fait | `Item` | `Subject` → `Item` sur le round, `Item.author` (inutilisé par le poker, mais le champ existe), migration de données, `Result.item`, `history` repointé, events `item.*` + `round.select`, `state.sync.items[]`, alias hérités. **Back seul** : les alias rendent le front inchangé, il bascule en 5b. | e2e `vote-cycle`, `round-flow`, `team-room` verts **contre le dépôt front non modifié**. |
| **5b** ✅ fait | `Response` | `Vote` → `Response` + `payload` + `item`, unicité `(item, participant)`, agrégation par item, `response.cast`. Suppression des alias 5a. | Un round poker à 2 items se dépouille item par item. |
| **5c** ✅ fait | Type par round | `Round.config` (deck figé dès la préparation, sur le round — pas de `vote_type` en FK, voir §3), validation par le registre (`config_schema`), `round.configure`. `items_authored_by` générique **n'a pas été livré** : le poker reste facilitateur-seul via la garde existante (`_require_facilitator`), sans mécanisme par activité — reporté. | Deux rounds de types différents dans une même room, chacun gardant son deck, son dépouillement et son résultat ; `item.add` refusé à un votant sur un round poker. |
| **5d** | Scénario préparé | `Round.sequence`, file de rounds, `scenario.*`, écran de préparation front. | Un scénario de 3 rounds préparé avant l'ouverture de la room, joué dans l'ordre. |
| **5e** | Chaînage | `source_round`, `source_rule` (`auto` et `manual`), résolution en copie, `origin_item`, recopie de `author`, garde-fous du registre, écran de sélection manuelle côté front. | Round 1 poker → round 2 alimenté par ses résultats, en auto **et** en manuel. |

Dot Voting (étape 6) vient après 5e et, si le découpage tient sa promesse, ne
touche que le registre plus deux composants Angular.

## 9. Risques

- **Couplage back/front.** 5a et 5b changent le contrat. Les alias hérités
  autorisent un déploiement décalé de quelques heures, pas davantage. Merger le
  back avant le front, jamais l'inverse (le front tolère un champ inconnu, le back
  rejette un type inconnu).
- **Migration sur PostgreSQL.** Les trois cas du §4 doivent être couverts par des
  tests de migration ; sqlite ne les départagera pas.
- **Tests async du consumer.** Toute nouvelle attente se fait par aller-retour
  (`ping` / `pong`), jamais par `asyncio.sleep` — piège documenté dans `CLAUDE.md`.
- **`Result` reste `chosen_value`.** Première activité produisant autre chose
  qu'une valeur de carte → il faudra le passer en payload. Le sortir du périmètre
  est délibéré, pas un oubli.
- **Contrainte `(item, participant)`.** Elle remplace `(round, participant)` : la
  migration doit vérifier qu'aucun round existant n'a deux votes du même
  participant (impossible aujourd'hui par construction, à confirmer en données).
