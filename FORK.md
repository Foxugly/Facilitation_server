# Fork de Poker_server → Facilitation_server

Copie de `Foxugly/Poker_server` @ main. **Poker_server n'est pas modifié.**
Le fork ne change que l'identité d'infrastructure. Aucun modèle, aucune vue,
aucune logique métier n'a été touchée. `pytest` : 240 passed.

## Ce qui a été renommé

| Avant | Après |
|---|---|
| `poker.foxugly.com` | `facilitation.foxugly.com` |
| `poker-api.foxugly.com` | `facilitation-api.foxugly.com` |
| port `8006` | port `8007` |
| `Celery("poker")` | `Celery("facilitation")` |
| `getLogger("poker")` | `getLogger("facilitation")` |
| `poker.event` / `poker_event` (routage Channels) | `facilitation.event` / `facilitation_event` |
| tâche beat `poker-expire-stale-rooms` | `facilitation-expire-stale-rooms` |
| SSM `/poker-server/` | `/facilitation-server/` |
| `deploy/systemd/poker-*.service` | `facilitation-*.service` |
| `deploy/nginx/poker-api.conf` | `facilitation-api.conf` |
| `BILLING_APP_SLUG` = `poker` | `facilitation` |
| titre OpenAPI, `DEFAULT_FROM_EMAIL` | Facilitation |

## Ce qui a été laissé intact, volontairement

« Delegation Poker » comme **nom d'activité** : le code `VoteType` `delegation_poker`,
`seed_delegation_deck`, les decks, les docstrings métier. C'est du contenu produit,
pas de la marque projet — Delegation Poker reste la première activité de Facilitation.

Les `docs/superpowers/specs/` sont conservées telles quelles : le contrat temps réel
et la spec de modèle de données restent la référence à étendre.

## À faire manuellement avant tout déploiement

1. Créer le repo GitHub et pousser (voir plus bas).
2. Enregistrer le slug `facilitation` auprès du service de facturation central
   (`billing-api.foxugly.com`) — sinon aucun droit payant ne sera résolu.
3. Créer les paramètres SSM `/facilitation-server/prod/*`
   (`deploy/seed-parameter-store.sh` en donne la liste).
4. DNS + certificat pour `facilitation.foxugly.com` et `facilitation-api.foxugly.com`.
5. Base de données dédiée. Les migrations sont celles de Poker : la base part vide,
   il n'y a aucune donnée à reprendre.
6. Vérifier qu'aucun autre service de la flotte n'occupe le port 8007.

## Premier commit — fait

Le renommage `VoteSession` → `Round` a été passé sur ce fork
(`rooms/migrations/0009_votesession_to_round.py`) : modèle, champs
`Vote.round` / `Result.round` / `Room.current_round`, `related_name` `rounds`,
et contrainte `uniq_vote_round_participant`. `pytest` : 240 passed, inchangé.

Le type de message WebSocket `session.join` a été **laissé intact** : il appartient
au contrat temps réel (§4) et le renommer casserait `Facilitation_frontend` tant que
les deux dépôts ne sont pas livrés ensemble. C'est le seul « session » qui subsiste
dans le domaine.

Restent à faire, dans cet ordre : `Subject` → `Item`, puis `Vote` → `Response`
(étape 5 du plan — voir `CLAUDE.md`).

## Pousser vers GitHub

```
cd Facilitation_server
git remote add origin https://github.com/Foxugly/Facilitation_server.git
git push -u origin main
```
