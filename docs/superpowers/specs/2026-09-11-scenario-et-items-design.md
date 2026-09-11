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
   jouée sur N items.
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
| Type d'activité | Par round (`Round.vote_type` + `Round.config`). `Room.vote_type` n'est plus que le défaut à la création. |
| Chaînage | Liaison déclarée (`Round.source_round` + règle), **copie** des items au démarrage, `Item.origin_item` pour la traçabilité. Jamais une référence vivante. |
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
- `vote_type` (FK `decks.VoteType`) : l'activité jouée. Renseigné à la création
  depuis `Room.vote_type`.
- `config` (JSON, défaut `{}`) : la configuration de l'activité, validée par le
  schéma que déclare le registre.
- `source_round` (FK self, null) et `source_rule` (JSON, null) : la liaison de
  chaînage.
- perd `subject` (FK unique) au profit du `related_name` `items`.

**`Item`** — ex-`Subject`, rattaché au **round** :

- `round` (FK, CASCADE), `text` (300), `sequence`, `created_at`.
- `origin_item` (FK self, null, SET_NULL) : l'item dont celui-ci est la copie.

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

### Entrants (facilitateur sauf mention)

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

`realtime/activities.py` — `ActivitySpec` gagne quatre champs, et reste la
**seule** chose à toucher pour ajouter une activité :

- `config_schema` / `payload_schema` : validation de `Round.config` et de
  `Response.payload`.
- `aggregate(responses, items) -> results` : l'agrégateur, aujourd'hui dispersé
  dans `services.revealed_payload`.
- `consumes` / `produces` (`items` / `results` / `none`) : ce que le chaînage a le
  droit de brancher sur quoi. Le registre front déclare déjà ces deux champs, avec
  `consumes: 'subjects'` — à renommer en `'items'` dans la livraison 5a, pour que
  les deux registres parlent le même vocabulaire.

La clef reste `VoteType.resolution_strategy` côté serveur (deux decks peuvent
partager une résolution) et `VoteType.code` côté front (le rendu, lui, est
propre au type). Cette asymétrie est voulue ; la documenter dans les deux
fichiers.

## 7. Chaînage

À la préparation, un round déclare sa source :

```
Round.source_round = <round amont>
Round.source_rule  = {"take": "results" | "items", "top": 3 | null}
```

Au moment où le round devient courant, le serveur **copie** : chaque élément
retenu de la source devient un `Item` du round consommateur, `origin_item`
renseigné. Le facilitateur peut ensuite éditer, supprimer, réordonner ces items
avant d'ouvrir — c'est une copie, la source n'en sait rien.

- Un round sans source : items saisis à la main (cas du poker).
- « Send to Dot Voting » à chaud = la même opération, déclenchée pendant la séance
  plutôt que déclarée d'avance. Un seul chemin de code.
- Source encore ouverte : autorisée (copie de l'état à l'instant T).
- Ré-ouvrir la source **n'actualise pas** les copies déjà faites ; l'UI doit le
  signaler. Décision reprise telle quelle de `CLAUDE.md`.
- Le registre refuse une liaison dont le `produces` de la source ne correspond pas
  au `consumes` de la cible.

## 8. Découpage

Chaque livraison : une PR, `pytest` vert (référence actuelle **240 passed**), le
poker jouable de bout en bout, et les e2e front verts quand le contrat bouge.

| # | Livraison | Contenu | Vérification |
|---|---|---|---|
| **5a** | `Item` | `Subject` → `Item` sur le round, migration de données, `Result.item`, `history` repointé, events `item.*` + `round.select`, `state.sync.items[]`, alias hérités. Front : `items[]` au lieu de `subject`. | e2e `vote-cycle`, `round-flow`, `team-room` verts sans modification fonctionnelle visible. |
| **5b** | `Response` | `Vote` → `Response` + `payload` + `item`, unicité `(item, participant)`, agrégation par item, `response.cast`. Suppression des alias 5a. | Un round poker à 2 items se dépouille item par item. |
| **5c** | Type par round | `Round.vote_type` + `config`, validation par le registre, `round.configure`. | Deux rounds de types différents dans une même room. |
| **5d** | Scénario préparé | `Round.sequence`, file de rounds, `scenario.*`, écran de préparation front. | Un scénario de 3 rounds préparé avant l'ouverture de la room, joué dans l'ordre. |
| **5e** | Chaînage | `source_round`, `source_rule`, résolution en copie, `origin_item`, garde-fous du registre. | Round 1 poker → round 2 alimenté par ses résultats. |

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
