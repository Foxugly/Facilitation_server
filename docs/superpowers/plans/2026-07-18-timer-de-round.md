# Timer de round — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** un timer de round optionnel — le facilitateur l'active et règle sa durée ; à zéro, le serveur gèle les votes et révèle.

**Architecture :** l'échéance est calculée et persistée côté serveur à l'ouverture du vote, puis diffusée. Le décompte affiché par le client est cosmétique. La révélation à échéance est déclenchée par une tâche `asyncio` dans le processus ASGI, doublée d'une réconciliation paresseuse en base pour survivre à un redémarrage.

**Tech Stack :** Django 6 + Channels (ASGI) + pytest ; Angular 21 standalone + signals + PrimeNG + Transloco + Vitest.

**Spec de référence :** `docs/superpowers/specs/2026-07-18-timer-de-round-design.md`

## Global Constraints

- **Le serveur fait autorité sur l'échéance.** Le client affiche, il ne décide jamais. Un vote reçu après l'échéance est refusé côté serveur même si le client affiche encore du temps.
- **Durées admises : 10 à 60 secondes, par pas de 5** (onze valeurs : 10, 15, 20 … 60). Le serveur arrondit au multiple de 5 le plus proche, puis borne. Défaut `timer_enabled=False`, `timer_seconds=10`.
- **Réglage porté par la `Room`**, pas par le round : il persiste d'un round à l'autre.
- **Protocole rétrocompatible.** `PROTOCOL_VERSION` reste à `1` : on n'ajoute que des champs optionnels et une intention. Un client ancien doit continuer de fonctionner sans timer.
- **Réutiliser l'événement `vote.revealed` existant** pour la révélation à échéance, enrichi de `reason: "timeout" | "facilitator"`. Ne pas créer un second chemin de révélation côté client.
- **La tâche asyncio est rattachée à la room, jamais au consumer d'un client** : elle doit survivre à la déconnexion de celui qui a ouvert le vote.
- **Pas de quorum dans ce lot.** Révéler manuellement reste possible avec un seul vote. Le timer traite le temps, pas la participation.
- Tests backend : pytest, `@pytest.mark.django_db`, fixtures locales par fichier, URLs en dur. Aucun linter dans ce repo. Commande : `.venv/Scripts/python -m pytest`.
- Tests frontend : Vitest, **aucun `TestBed` dans ce repo** — instanciation directe ou fonctions pures. `npm test` **et** `npm run build` (le build ne typecheck pas les specs).
- i18n : cinq catalogues `public/i18n/{fr,nl,en,it,es}.json`. `src/app/i18n-parity.spec.ts` casse le build en cas d'oubli.
- **Fichier hors périmètre**, modifié-non-commité et appartenant au propriétaire : `Poker_frontend/src/app/features/about/contact.ts`. Ne pas le committer. Stager par chemins explicites.
- Branches : `feat/bypass-and-tables` (`Poker_server`), `feat/subscription-bypass` (`Poker_frontend`). Ne rien pousser — les deux repos auto-déploient.

---

### Task 1 : modèle, réglage et échéance

**Repo :** `D:\Projects\PycharmProjects\Poker_server`

**Files:**
- Modify: `rooms/models.py` (`Room`, `VoteSession`)
- Create: `rooms/migrations/0006_*.py` (généré)
- Modify: `realtime/services.py`
- Test: `realtime/tests/test_timer.py` (nouveau)

**Interfaces:**
- Produit : `Room.timer_enabled: bool`, `Room.timer_seconds: int`, `VoteSession.vote_deadline: datetime | None` ; `services.set_timer(room, participant, enabled, seconds) -> dict`, `services.open_vote(room, participant) -> datetime | None` (change de signature : renvoyait `None`), `services.deadline_iso(room) -> str | None`.

- [ ] **Step 1 : écrire les tests qui échouent**

Créer `realtime/tests/test_timer.py`. S'inspirer de `realtime/tests/test_consumer.py` pour la construction d'une room et de participants.

```python
"""Timer de round : reglage facilitateur, echeance posee a l'ouverture, votes tardifs refuses."""
import pytest
from django.utils import timezone

from realtime import services
from realtime.services import RoomError
from rooms.models import RoundState


@pytest.mark.django_db
def test_timer_defaults_to_disabled_at_ten_seconds(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    assert room.timer_enabled is False and room.timer_seconds == 10


@pytest.mark.django_db
def test_set_timer_requires_facilitator(room_with_facilitator):
    room, _, voter = room_with_facilitator
    with pytest.raises(RoomError):
        services.set_timer(room, voter, True, 30)


@pytest.mark.django_db
def test_set_timer_clamps_out_of_range(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    assert services.set_timer(room, facilitator, True, 5)["seconds"] == 10
    assert services.set_timer(room, facilitator, True, 9999)["seconds"] == 60


@pytest.mark.django_db
def test_set_timer_snaps_to_five_second_steps(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    assert services.set_timer(room, facilitator, True, 37)["seconds"] == 35
    assert services.set_timer(room, facilitator, True, 38)["seconds"] == 40
    assert services.set_timer(room, facilitator, True, 15)["seconds"] == 15


@pytest.mark.django_db
def test_open_vote_sets_no_deadline_when_disabled(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    services.set_subject(room, facilitator, "Recrutement")
    assert services.open_vote(room, facilitator) is None
    assert services._current_session(room).vote_deadline is None


@pytest.mark.django_db
def test_open_vote_sets_deadline_when_enabled(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    services.set_timer(room, facilitator, True, 30)
    services.set_subject(room, facilitator, "Recrutement")
    before = timezone.now()
    deadline = services.open_vote(room, facilitator)
    assert deadline is not None
    delta = (deadline - before).total_seconds()
    assert 29 <= delta <= 31


@pytest.mark.django_db
def test_vote_after_deadline_is_refused(room_with_facilitator):
    room, facilitator, voter = room_with_facilitator
    services.set_timer(room, facilitator, True, 30)
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    session = services._current_session(room)
    session.vote_deadline = timezone.now() - timezone.timedelta(seconds=1)
    session.save(update_fields=["vote_deadline"])
    with pytest.raises(RoomError):
        services.cast_vote(room, voter, "4")


@pytest.mark.django_db
def test_reset_clears_the_deadline(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    services.set_timer(room, facilitator, True, 30)
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    services.reset_round(room, facilitator)
    assert services._current_session(room).vote_deadline is None
    assert services._current_session(room).state == RoundState.IDLE
```

Ajouter en tête du fichier une fixture locale `room_with_facilitator` construisant une room (avec `standard_deck`), un participant facilitateur et un participant votant — calquée sur ce que fait déjà `realtime/tests/test_consumer.py`. **Ouvrir ce fichier d'abord** pour reprendre sa méthode de construction plutôt que d'en inventer une.

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest realtime/tests/test_timer.py -q`
Expected: FAIL — `AttributeError: 'Room' object has no attribute 'timer_enabled'`

- [ ] **Step 3 : ajouter les champs**

Dans `rooms/models.py`, dans `Room`, après `max_participants` :

```python
    # Timer de round (optionnel) : le facilitateur l'active et regle sa duree.
    # Porte par la room et non par le round, pour persister d'un round a l'autre.
    # Duree bornee 10-60 s par pas de 5, normalisee cote serveur (services.set_timer).
    timer_enabled = models.BooleanField(default=False)
    timer_seconds = models.PositiveSmallIntegerField(default=10)
```

et dans `VoteSession`, après `revealed_at` :

```python
    # Echeance du vote, posee a l'ouverture quand le timer est actif. Le serveur
    # fait autorite : le decompte affiche par le client est cosmetique.
    vote_deadline = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4 : implémenter le réglage et l'échéance**

Dans `realtime/services.py`, ajouter les bornes en tête de module :

```python
TIMER_MIN_SECONDS = 10
TIMER_MAX_SECONDS = 60
TIMER_STEP_SECONDS = 5
```

puis la fonction de réglage :

```python
def set_timer(room, participant, enabled, seconds):
    """Reglage du timer par le facilitateur. La duree est normalisee cote serveur
    (arrondi au multiple de 5 le plus proche, puis bornage 10-60) : un client
    modifie ne peut imposer ni 0 s, ni une valeur absurde, ni un pas hors grille."""
    _require_facilitator(room, participant, "timer.set")
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = room.timer_seconds
    seconds = round(seconds / TIMER_STEP_SECONDS) * TIMER_STEP_SECONDS
    seconds = max(TIMER_MIN_SECONDS, min(TIMER_MAX_SECONDS, seconds))
    room.timer_enabled = bool(enabled)
    room.timer_seconds = seconds
    room.save(update_fields=["timer_enabled", "timer_seconds"])
    room.touch()
    return {"enabled": room.timer_enabled, "seconds": room.timer_seconds}
```

Modifier `open_vote` (lignes 150-160) pour poser l'échéance et la renvoyer :

```python
def open_vote(room, participant):
    _require_facilitator(room, participant, "vote.open")
    session = _current_session(room)
    if session is None or not session.subject.text.strip():
        raise RoomError("state.invalid_transition", "No subject set", "vote.open")
    if session.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Not idle", "vote.open")
    session.state = RoundState.OPEN
    session.opened_at = timezone.now()
    session.vote_deadline = (
        session.opened_at + timezone.timedelta(seconds=room.timer_seconds)
        if room.timer_enabled
        else None
    )
    session.save(update_fields=["state", "opened_at", "vote_deadline"])
    room.touch()
    return session.vote_deadline
```

Modifier `cast_vote` (ligne 163) pour refuser un vote tardif, juste après le contrôle d'état :

```python
    if session.vote_deadline is not None and timezone.now() > session.vote_deadline:
        raise RoomError("state.invalid_transition", "Voting time is over", "vote.cast")
```

Modifier `reset_round` (ligne 205) pour effacer l'échéance : ajouter `session.vote_deadline = None` et inclure `"vote_deadline"` dans `update_fields`.

Ajouter enfin un accesseur utilisé par le consumer :

```python
def deadline_iso(room):
    """Echeance du round courant au format ISO, ou None. Sert aux payloads WS."""
    session = _current_session(room)
    if session is None or session.vote_deadline is None:
        return None
    return session.vote_deadline.isoformat()
```

- [ ] **Step 5 : générer la migration**

Run: `.venv/Scripts/python manage.py makemigrations rooms`
Expected: une migration ajoutant les trois champs. Ne pas l'écrire à la main.

- [ ] **Step 6 : lancer les tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, aucune régression

- [ ] **Step 7 : commit**

```bash
git add rooms/ realtime/services.py realtime/tests/test_timer.py
git commit -m "feat(timer): reglage facilitateur et echeance de round cote serveur"
```

---

### Task 2 : révélation à échéance

**Repo :** `D:\Projects\PycharmProjects\Poker_server`

**Files:**
- Modify: `realtime/services.py`
- Modify: `realtime/consumers.py`
- Test: `realtime/tests/test_timer.py` (ajouts)

**Interfaces:**
- Consomme : `Room.timer_enabled/timer_seconds`, `VoteSession.vote_deadline`, `services.open_vote`, `services.deadline_iso` (Task 1).
- Produit : `services.reveal_on_timeout(room) -> bool` ; intention client `timer.set` ; événement serveur `timer.changed` ; champ `deadline` dans `vote.opened` et `state.sync` ; champ `reason` dans `vote.revealed`.

**Deux décisions à respecter :**
- La révélation à échéance **contourne le garde « No votes yet »** de `reveal()` : ce garde protège d'une révélation prématurée par erreur, alors qu'une expiration est délibérée. `revealed_payload` gère déjà une liste vide (`spread` à `None`).
- La tâche asyncio est stockée dans un **dictionnaire au niveau du module**, clé = code de room, jamais sur l'instance du consumer. Une tâche existante pour la même room est annulée avant d'en programmer une nouvelle.

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter à `realtime/tests/test_timer.py` :

```python
@pytest.mark.django_db
def test_reveal_on_timeout_reveals_when_deadline_passed(room_with_facilitator):
    room, facilitator, voter = room_with_facilitator
    services.set_timer(room, facilitator, True, 30)
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    services.cast_vote(room, voter, "4")
    session = services._current_session(room)
    session.vote_deadline = timezone.now() - timezone.timedelta(seconds=1)
    session.save(update_fields=["vote_deadline"])

    assert services.reveal_on_timeout(room) is True
    assert services._current_session(room).state == RoundState.REVEALED


@pytest.mark.django_db
def test_reveal_on_timeout_is_a_noop_before_deadline(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    services.set_timer(room, facilitator, True, 30)
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)

    assert services.reveal_on_timeout(room) is False
    assert services._current_session(room).state == RoundState.OPEN


@pytest.mark.django_db
def test_reveal_on_timeout_works_with_zero_votes(room_with_facilitator):
    """Une expiration est deliberee : elle revele meme sans aucun vote, contrairement
    a une revelation manuelle qui exige au moins un vote."""
    room, facilitator, _ = room_with_facilitator
    services.set_timer(room, facilitator, True, 30)
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    session = services._current_session(room)
    session.vote_deadline = timezone.now() - timezone.timedelta(seconds=1)
    session.save(update_fields=["vote_deadline"])

    assert services.reveal_on_timeout(room) is True
    assert services.revealed_payload(room)["votes"] == []


@pytest.mark.django_db
def test_reveal_on_timeout_is_a_noop_without_timer(room_with_facilitator):
    room, facilitator, _ = room_with_facilitator
    services.set_subject(room, facilitator, "Recrutement")
    services.open_vote(room, facilitator)
    assert services.reveal_on_timeout(room) is False
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest realtime/tests/test_timer.py -q`
Expected: FAIL — `AttributeError: module 'realtime.services' has no attribute 'reveal_on_timeout'`

- [ ] **Step 3 : implémenter la révélation à échéance**

Dans `realtime/services.py`, après `reveal` :

```python
def reveal_on_timeout(room):
    """Revele si l'echeance est depassee et que le round est encore ouvert.
    Renvoie True si une revelation a bien eu lieu, False sinon.

    Volontairement sans controle de facilitateur (c'est le serveur qui agit) et
    sans le garde "No votes yet" de reveal() : une expiration est deliberee,
    alors que ce garde protege d'une revelation manuelle prematuree.
    Idempotent : rappelable sans risque, ce dont depend la reconciliation
    paresseuse apres un redemarrage du service.
    """
    session = _current_session(room)
    if session is None or session.state != RoundState.OPEN:
        return False
    if session.vote_deadline is None or timezone.now() < session.vote_deadline:
        return False
    session.state = RoundState.REVEALED
    session.revealed_at = timezone.now()
    session.save(update_fields=["state", "revealed_at"])
    room.touch()
    return True
```

- [ ] **Step 4 : câbler le consumer**

Dans `realtime/consumers.py`, ajouter en tête du module :

```python
import asyncio

# Taches de revelation a echeance, par code de room. Au niveau du module et non
# sur l'instance du consumer : la tache doit survivre a la deconnexion du client
# qui a ouvert le vote.
_timer_tasks = {}
```

Remplacer la branche `vote.open` (lignes 74-77) :

```python
        elif mtype == "vote.open":
            deadline = await database_sync_to_async(services.open_vote)(room, participant)
            await self._broadcast("vote.opened", {"deadline": deadline.isoformat() if deadline else None})
            await self._broadcast_participation(room)
            self._schedule_timeout(room.code, deadline)
```

Ajouter la branche `timer.set` avant le `else` final :

```python
        elif mtype == "timer.set":
            settings_ = await database_sync_to_async(services.set_timer)(
                room, participant, payload.get("enabled"), payload.get("seconds")
            )
            await self._broadcast("timer.changed", settings_)
```

Enrichir la révélation manuelle (ligne 84) : `await self._broadcast("vote.revealed", {**revealed, "reason": "facilitator"})`.

Annuler la tâche sur `vote.reset` (après la ligne 90) : `self._cancel_timeout(room.code)`.

Ajouter les helpers dans la section `helpers` :

```python
    def _schedule_timeout(self, code, deadline):
        """Programme la revelation a echeance. Best-effort : la base fait foi via
        la reconciliation paresseuse de _reconcile_timeout()."""
        self._cancel_timeout(code)
        if deadline is None:
            return
        delay = max(0.0, (deadline - timezone.now()).total_seconds())
        _timer_tasks[code] = asyncio.create_task(self._fire_timeout(code, delay))

    def _cancel_timeout(self, code):
        task = _timer_tasks.pop(code, None)
        if task is not None:
            task.cancel()

    async def _fire_timeout(self, code, delay):
        try:
            await asyncio.sleep(delay)
            await self._reconcile_timeout(code)
        except asyncio.CancelledError:
            pass
        finally:
            _timer_tasks.pop(code, None)

    async def _reconcile_timeout(self, code):
        """Revele si l'echeance est passee. Appelee par la tache programmee ET a la
        reconnexion : un redemarrage du service perd la tache, pas l'echeance."""
        room = await database_sync_to_async(services.room_by_code)(code)
        if room is None:
            return
        fired = await database_sync_to_async(services.reveal_on_timeout)(room)
        if not fired:
            return
        revealed = await database_sync_to_async(services.revealed_payload)(room)
        await self._broadcast("vote.revealed", {**revealed, "reason": "timeout"})
```

Importer `timezone` (`from django.utils import timezone`) en tête du fichier s'il n'y est pas.

Si `services.room_by_code(code)` n'existe pas, l'ajouter dans `services.py` — une simple recherche par code normalisé renvoyant `None` si absente. **Vérifier avant** : `resolve_participant` fait déjà une recherche par code, il existe peut-être déjà un helper à réutiliser.

Enfin, dans `_handle_join` (ligne 104), déclencher la réconciliation **après** l'envoi de `state.sync`, pour que celui qui se reconnecte sur un round expiré le voie se révéler :

```python
        await self._broadcast_presence(participant.room)
        await self._reconcile_timeout(self.code)
```

- [ ] **Step 5 : exposer l'échéance dans `state.sync`**

Dans `services.build_state_sync`, ajouter au dictionnaire renvoyé les clés `"deadline": deadline_iso(room)` et `"timer": {"enabled": room.timer_enabled, "seconds": room.timer_seconds}`. **Ouvrir la fonction d'abord** pour respecter sa forme existante et le nommage camelCase de ses clés.

- [ ] **Step 6 : lancer les tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, aucune régression

- [ ] **Step 7 : commit**

```bash
git add realtime/
git commit -m "feat(timer): revelation a echeance + reconciliation a la reconnexion"
```

---

### Task 3 : décompte et réglage côté SPA

**Repo :** `D:\Projects\WebstormProjects\Poker_frontend`

**Files:**
- Modify: `src/app/core/realtime/room-socket.service.ts` (et son `.models.ts` si les types y vivent)
- Create: `src/app/core/realtime/countdown.ts`
- Create: `src/app/core/realtime/countdown.spec.ts`
- Modify: `src/app/features/room/room.component.ts`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`

**Interfaces:**
- Consomme : `vote.opened` avec `deadline` (ISO ou `null`), `state.sync` avec `deadline` et `timer: {enabled, seconds}`, `vote.revealed` avec `reason`, `timer.changed` avec `{enabled, seconds}`. Intention émise : `timer.set` avec `{enabled, seconds}`.

**Note :** `room-socket.service.spec.ts` existe déjà et teste le réducteur d'état sans `TestBed`. C'est le modèle à suivre — compléter ce spec plutôt que d'en créer un parallèle pour la partie réducteur.

- [ ] **Step 1 : écrire le test qui échoue**

Le décompte est une fonction pure du couple (échéance, instant courant), donc testable sans horloge factice ni DOM :

```ts
import { describe, expect, it } from 'vitest';
import { secondsLeft } from './countdown';

describe('secondsLeft', () => {
  const now = new Date('2026-07-18T12:00:00Z');

  it('renvoie null sans echeance', () => {
    expect(secondsLeft(null, now)).toBeNull();
  });

  it('arrondit vers le haut le temps restant', () => {
    expect(secondsLeft('2026-07-18T12:00:29.400Z', now)).toBe(30);
  });

  it('ne descend jamais sous zero', () => {
    expect(secondsLeft('2026-07-18T11:59:00Z', now)).toBe(0);
  });
});
```

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test`
Expected: FAIL — module `./countdown` introuvable

- [ ] **Step 3 : écrire la fonction pure**

Créer `src/app/core/realtime/countdown.ts` :

```ts
/** Secondes restantes avant l'echeance, ou null s'il n'y a pas de timer.
 *  Purement cosmetique : le serveur fait autorite sur l'expiration reelle. */
export function secondsLeft(deadline: string | null, now: Date = new Date()): number | null {
  if (!deadline) return null;
  const remaining = (new Date(deadline).getTime() - now.getTime()) / 1000;
  return Math.max(0, Math.ceil(remaining));
}
```

- [ ] **Step 4 : brancher le décompte dans la room**

Dans le service temps réel, conserver `deadline` et `timer` dans l'état de la room à la réception de `state.sync`, `vote.opened` et `timer.changed`.

Dans `room.component.ts`, exposer un signal de secondes restantes, rafraîchi par un `setInterval` de 1 s **démarré uniquement quand une échéance existe** et arrêté au reveal comme à la destruction du composant (`DestroyRef`). Afficher le décompte près du bloc de vote. Aucune logique de révélation côté client : quand le serveur envoie `vote.revealed`, l'UI suit, qu'il ait mis `reason: "timeout"` ou `"facilitator"`.

Ajouter, visible du seul facilitateur, un interrupteur d'activation et un sélecteur de durée émettant `timer.set`. Le pas de 5 s impose un **sélecteur de valeurs discrètes** (les onze valeurs 10, 15 … 60) plutôt qu'une saisie libre — c'est plus simple à utiliser et ça rend impossible une valeur hors grille. Le serveur normalise de toute façon : l'UI ne s'y substitue pas.

- [ ] **Step 5 : clés i18n dans les cinq catalogues**

| Clé | fr | en | nl | it | es |
|---|---|---|---|---|---|
| `room.timer.label` | Minuteur | Timer | Timer | Timer | Temporizador |
| `room.timer.seconds` | Durée (secondes) | Duration (seconds) | Duur (seconden) | Durata (secondi) | Duración (segundos) |
| `room.timer.remaining` | Temps restant | Time left | Resterende tijd | Tempo rimanente | Tiempo restante |
| `room.timer.expired` | Temps écoulé | Time is up | Tijd is om | Tempo scaduto | Se acabó el tiempo |

- [ ] **Step 6 : lancer les tests et le build**

Run: `npm test`
Expected: PASS, parité i18n incluse

Run: `npm run build`
Expected: succès

- [ ] **Step 7 : commit**

```bash
git add src/app/core/realtime/ src/app/features/room/ public/i18n/
git commit -m "feat(timer): decompte de round et reglage facilitateur"
```

---

## Vérification finale

- [ ] `.venv/Scripts/python -m pytest -q` vert dans `Poker_server`
- [ ] `npm test && npm run build` verts dans `Poker_frontend`
- [ ] Scénario manuel à deux navigateurs : activer le timer à 15 s, ouvrir un vote, voter d'un seul côté, laisser expirer — les **deux** écrans doivent basculer en révélé simultanément, et un vote tenté après expiration doit être refusé par le serveur.
- [ ] Scénario de redémarrage : ouvrir un vote avec timer, redémarrer le service backend avant l'échéance, recharger la page — le round doit se révéler à la reconnexion (réconciliation paresseuse), pas rester ouvert indéfiniment.
