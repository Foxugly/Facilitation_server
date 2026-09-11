"""Synchronous domain logic for the realtime room (data-model spec §5.4, contract §4-§6).

Kept sync + framework-light so it is unit-testable without a socket; the consumer
wraps these with ``database_sync_to_async``. Server is the source of truth: every
mutation validates the state machine and raises ``RoomError`` on an illegal move
(contract §0.1, §6.b) rather than applying it.
"""
from collections import Counter

from django.conf import settings
from django.utils import timezone

from realtime.activities import is_ordinal

from rooms.models import (
    Item,
    Participant,
    Result,
    Role,
    Room,
    RoundState,
    Subject,
    Vote,
    Round,
)


TIMER_MIN_SECONDS = 10
TIMER_MAX_SECONDS = 60
TIMER_STEP_SECONDS = 5


class RoomError(Exception):
    def __init__(self, code, message="", rejected_type=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.rejected_type = rejected_type


def _card_values(room):
    """Deck values, in deck order (``deck_snapshot["cards"]`` is built already sorted
    by ``Card.order`` — see ``rooms.snapshot.build_deck_snapshot``). Returned as a
    list, not a set: callers that display a per-value tally (``revealed_payload``)
    depend on this order being stable across reveals."""
    rnd = room.current_round
    snapshot = (rnd.deck_snapshot if rnd and rnd.deck_snapshot else room.deck_snapshot)
    return [card["value"] for card in (snapshot or {}).get("cards", [])]


# Principe P1 de la spec : la DB decrit un type de vote, le code decide du
# comportement. Ce comportement vit desormais dans `realtime.activities`, qui le
# rassemble au lieu de le disperser — voir l'en-tete de ce module.


def _resolution_strategy(room):
    """La strategie du deck actif — le round en cours s'il porte un snapshot, sinon
    celui de la salle. Meme regle de priorite que ``_card_values``."""
    rnd = room.current_round
    snapshot = (rnd.deck_snapshot if rnd and rnd.deck_snapshot else room.deck_snapshot)
    return (snapshot or {}).get("resolutionStrategy", "")


def _spread_for(strategy, card_values):
    """Ecart min/max des votes, ou {None, None} si l'echelle n'est pas ordinale.

    Sans ce garde-fou, un vote romain calculerait son ecart sur les seules valeurs
    passant isdigit() : « +1 » et « -1 » echouent, « 0 » reussit, et l'ecran
    afficherait « 0 - 0 » — un faux consensus — sous un vote pourtant partage.
    """
    if not is_ordinal(strategy):
        return {"min": None, "max": None}
    numeric = [int(v) for v in card_values if v.isdigit()]
    if not numeric:
        return {"min": None, "max": None}
    return {"min": min(numeric), "max": max(numeric)}


def resolve_participant(code, token):
    """token → participant (+ room). Returns None if unknown/expired (contract §3)."""
    participant = (
        Participant.objects.select_related("room")
        .filter(room__code=code, token=token)
        .first()
    )
    if participant is None:
        return None
    if not participant.room.is_live:
        return None
    return participant


def room_by_code(code):
    """code → room, or None if unknown/expired. Used by the consumer's timeout
    reconciliation, which has no participant/token in hand (background task)."""
    room = Room.objects.filter(code=code).first()
    if room is None or not room.is_live:
        return None
    return room


def set_connected(participant, connected):
    participant.is_connected = connected
    participant.last_seen_at = timezone.now()
    participant.save(update_fields=["is_connected", "last_seen_at"])


def current_round(room):
    return room.current_round


def _require_facilitator(room, participant, rejected_type):
    rnd = current_round(room)
    # Authority is the round facilitator; before any round exists, the room's
    # sole facilitator participant holds it (contract §2).
    if rnd and rnd.facilitator_id:
        if participant.id != rnd.facilitator_id:
            raise RoomError("forbidden.not_facilitator", "Not the facilitator", rejected_type)
    elif participant.role != Role.FACILITATOR:
        raise RoomError("forbidden.not_facilitator", "Not the facilitator", rejected_type)


def touch(room):
    room.touch()


def items_payload(rnd):
    """Les items d'un round, dans l'ordre du facilitateur."""
    if rnd is None:
        return []
    return [{"id": i.id, "text": i.text, "sequence": i.sequence} for i in rnd.items.all()]


def _new_round(room, participant, text):
    """Un round neuf portant un premier item. Le scenario est une file de rounds :
    poser un nouveau sujet, c'est ouvrir un round de plus."""
    rnd = Round.objects.create(room=room, state=RoundState.IDLE, facilitator=participant)
    Item.objects.create(round=rnd, text=text, sequence=1)
    return rnd


def _first_item(rnd):
    return rnd.items.first() if rnd else None


def set_current_item(room, participant, text):
    """Ex-`set_subject`, semantique inchangee : on reecrit l'item du round courant
    s'il est encore idle, sinon on ouvre un round neuf."""
    _require_facilitator(room, participant, "item.update")
    rnd = current_round(room)
    first = _first_item(rnd)
    if rnd and rnd.state == RoundState.IDLE and first:
        first.text = text
        first.save(update_fields=["text"])
    else:
        rnd = _new_round(room, participant, text)
        room.current_round = rnd
        room.save(update_fields=["current_round"])
    room.touch()
    return text


def current_item_text(room):
    """Ex-`current_subject_text`."""
    first = _first_item(current_round(room))
    return first.text if first else ""


def add_item(room, participant, text):
    """Ajoute un item AU ROUND COURANT — le N-items du design §3."""
    _require_facilitator(room, participant, "item.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.add")
    rnd = current_round(room)
    if rnd is None:
        rnd = _new_round(room, participant, text)
        room.current_round = rnd
        room.save(update_fields=["current_round"])
        item = _first_item(rnd)
    else:
        seq = rnd.items.count() + 1
        item = Item.objects.create(round=rnd, text=text, sequence=seq)
    room.touch()
    return {"id": item.id, "text": item.text, "sequence": item.sequence}


def _item_of_room(room, item_id, rejected_type):
    item = Item.objects.filter(id=item_id, round__room=room).select_related("round").first()
    if item is None:
        raise RoomError("state.invalid_transition", "Unknown item", rejected_type)
    return item


def update_item(room, participant, item_id, text):
    _require_facilitator(room, participant, "item.update")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.update")
    item = _item_of_room(room, item_id, "item.update")
    item.text = text
    item.save(update_fields=["text"])
    room.touch()
    return {"id": item.id, "text": item.text, "sequence": item.sequence}


def remove_item(room, participant, item_id):
    _require_facilitator(room, participant, "item.remove")
    item = _item_of_room(room, item_id, "item.remove")
    # Un item deja acte porte un resultat fige : le retirer reecrirait
    # l'historique, que le design interdit explicitement.
    if item.results.exists():
        raise RoomError("state.invalid_transition", "Item already decided", "item.remove")
    rnd = item.round
    item.delete()
    for index, remaining in enumerate(rnd.items.all(), start=1):
        if remaining.sequence != index:
            remaining.sequence = index
            remaining.save(update_fields=["sequence"])
    room.touch()
    return item_id


def reorder_items(room, participant, item_ids):
    _require_facilitator(room, participant, "item.reorder")
    rnd = current_round(room)
    known = {i.id: i for i in (rnd.items.all() if rnd else [])}
    # len() en plus du set() : sans elle, [1, 1, 2] passe pour {1, 2} et laisse une
    # sequence non contigue -- le serveur fait autorite, pas de trou de validation.
    if len(item_ids) != len(known) or set(item_ids) != set(known):
        raise RoomError("state.invalid_transition", "Item set mismatch", "item.reorder")
    for index, item_id in enumerate(item_ids, start=1):
        item = known[item_id]
        item.sequence = index
        item.save(update_fields=["sequence"])
    room.touch()
    return items_payload(rnd)


def build_agenda(room):
    """Le scenario : chaque ROUND de la salle avec son etat et, s'il a ete acte, la
    valeur retenue. L'`id` est desormais un id de round — le front le renvoie tel
    quel, donc l'alias `subject.select` reste juste sans rien savoir du changement.
    """
    current_id = room.current_round_id
    out = []
    for rnd in room.rounds.all().order_by("created_at", "id").prefetch_related("items", "result"):
        first = rnd.items.first()
        result = rnd.result.chosen_value if hasattr(rnd, "result") else None
        status = "current" if rnd.id == current_id else ("done" if result is not None else "pending")
        out.append({
            "id": rnd.id,
            "text": first.text if first else "",
            "status": status,
            "result": result,
            "items": items_payload(rnd),
        })
    return out


def select_round(room, participant, round_id):
    """Ex-`select_subject` : reprendre un round du scenario le remet a idle."""
    _require_facilitator(room, participant, "round.select")
    rnd = room.rounds.filter(id=round_id).first()
    if rnd is None:
        raise RoomError("state.invalid_transition", "Unknown round", "round.select")
    if rnd.state != RoundState.IDLE:
        rnd.state = RoundState.IDLE
        rnd.opened_at = None
        rnd.revealed_at = None
        rnd.vote_deadline = None
        rnd.facilitator = participant
        rnd.save(update_fields=["state", "opened_at", "revealed_at", "vote_deadline", "facilitator"])
        rnd.votes.all().delete()
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    room.touch()
    first = rnd.items.first()
    return {"roundId": rnd.id, "items": items_payload(rnd), "text": first.text if first else ""}


def add_scenario_item(room, participant, text):
    """Ex-`add_subject` : ajoute une entree au scenario, donc un ROUND de plus.
    Retourne l'id du round cree — c'est lui que l'agenda designe."""
    _require_facilitator(room, participant, "item.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.add")
    rnd = _new_round(room, participant, text)
    if room.current_round_id is None:
        room.current_round = rnd
        room.save(update_fields=["current_round"])
    room.touch()
    return rnd.id


def set_timer(room, participant, enabled, seconds):
    """Reglage du timer par le facilitateur. La duree est normalisee cote serveur
    (arrondi au multiple de 5 le plus proche, puis bornage 10-60) : un client
    modifie ne peut imposer ni 0 s, ni une valeur absurde, ni un pas hors grille.

    Feature d'equipe uniquement : une salle anonyme n'a pas de timer (le panneau
    ne l'affiche pas ; un client modifie se voit refuser)."""
    _require_facilitator(room, participant, "timer.set")
    if room.team_id is None:
        raise RoomError("forbidden.subscription_required", "Timer requires a team room", "timer.set")
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


def prepare_round(
    room,
    participant,
    *,
    subject_id=None,
    subject_text=None,
    anonymous=None,
    deck_id=None,
    timer_enabled=None,
    timer_seconds=None,
):
    """Step 1 of the two-step round flow: compose and announce the next round in one
    atomic call — pick/set the subject, the deck, the reveal mode and the timer — but
    leave it IDLE (not open). Opening is a separate step (``open_vote``).

    Doing it atomically is what lets the facilitator manipulate the panel as a *form*
    (subject + details) and commit it in one go: every setting is applied while the
    round provably exists and is idle, so none of them can race or reject (that's
    what used to make toggling the reveal mode before any subject existed pop an
    error). Reuses the single-setting services so the rules stay in one place.
    """
    _require_facilitator(room, participant, "round.prepare")
    # 1) Make the chosen round current (creating/resetting it). `subject_id` designe
    # desormais un ROUND (build_agenda l'a toujours appele `id`) : l'alias vit sans
    # le savoir.
    if subject_id is not None:
        select_round(room, participant, subject_id)
    elif subject_text is not None and subject_text.strip():
        set_current_item(room, participant, subject_text.strip())
    rnd = current_round(room)
    if rnd is None or not (_first_item(rnd) and _first_item(rnd).text.strip()):
        raise RoomError("state.invalid_transition", "No subject set", "round.prepare")
    if rnd.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Round already started", "round.prepare")
    # 2) Details. Each helper re-checks facilitator/state; order is irrelevant now
    #    that the idle round exists.
    if deck_id is not None:
        select_deck(room, participant, deck_id)
    if anonymous is not None:
        set_reveal_mode(room, participant, anonymous)
    # Timer: team-only feature. Silently ignored (not refused) for an anonymous
    # room so a stale client sending timer fields can still prepare its round.
    if room.team_id is not None and (timer_enabled is not None or timer_seconds is not None):
        set_timer(
            room,
            participant,
            room.timer_enabled if timer_enabled is None else timer_enabled,
            room.timer_seconds if timer_seconds is None else timer_seconds,
        )
    room.refresh_from_db(fields=["deck_snapshot", "timer_enabled", "timer_seconds"])
    rnd.refresh_from_db(fields=["is_anonymous"])
    return {
        "subject": current_item_text(room),
        "deckSnapshot": active_deck_snapshot(room),
        "anonymous": bool(rnd.is_anonymous),
        "timerEnabled": room.timer_enabled,
        "timerSeconds": room.timer_seconds,
    }


def open_vote(room, participant):
    _require_facilitator(room, participant, "vote.open")
    rnd = current_round(room)
    if rnd is None or not (_first_item(rnd) and _first_item(rnd).text.strip()):
        raise RoomError("state.invalid_transition", "No subject set", "vote.open")
    if rnd.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Not idle", "vote.open")
    # Freeze the deck this round is played with: the room's active deck may change
    # afterwards, and the round's values must keep their meaning (history labels).
    rnd.deck_snapshot = room.deck_snapshot
    rnd.state = RoundState.OPEN
    rnd.opened_at = timezone.now()
    rnd.vote_deadline = (
        rnd.opened_at + timezone.timedelta(seconds=room.timer_seconds)
        if room.timer_enabled
        else None
    )
    rnd.save(update_fields=["deck_snapshot", "state", "opened_at", "vote_deadline"])
    room.touch()
    return rnd.vote_deadline


def cast_vote(room, participant, card_value):
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.OPEN:
        raise RoomError("state.invalid_transition", "Voting is not open", "vote.cast")
    if rnd.vote_deadline is not None and timezone.now() > rnd.vote_deadline:
        raise RoomError("state.invalid_transition", "Voting time is over", "vote.cast")
    if card_value not in _card_values(room):
        raise RoomError("state.invalid_transition", "Unknown card value", "vote.cast")
    Vote.objects.update_or_create(
        round=rnd, participant=participant, defaults={"card_value": card_value}
    )
    room.touch()


def reveal(room, participant):
    _require_facilitator(room, participant, "vote.reveal")
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.OPEN:
        raise RoomError("state.invalid_transition", "Not open", "vote.reveal")
    if not rnd.votes.exists():
        raise RoomError("state.invalid_transition", "No votes yet", "vote.reveal")
    rnd.state = RoundState.REVEALED
    rnd.revealed_at = timezone.now()
    rnd.save(update_fields=["state", "revealed_at"])
    room.touch()


def reveal_on_timeout(room):
    """Revele si l'echeance est depassee et que le round est encore ouvert.
    Renvoie True si une revelation a bien eu lieu, False sinon.

    Volontairement sans controle de facilitateur (c'est le serveur qui agit) et
    sans le garde "No votes yet" de reveal() : une expiration est deliberee,
    alors que ce garde protege d'une revelation manuelle prematuree.
    Idempotent : rappelable sans risque, ce dont depend la reconciliation
    paresseuse apres un redemarrage du service.

    Transition atomique (UPDATE conditionnel) : deux appels concurrents (deux
    taches asyncio dans le meme process, ou deux process ASGI distincts avec
    chacun son propre dictionnaire de taches) ne doivent PAS tous les deux
    renvoyer True, sinon `vote.revealed` part deux fois vers la room. Le
    read-check-write nu (lire l'etat, puis sauvegarder) laisse une fenetre ou
    les deux lisent OPEN avant que l'un des deux n'ecrive REVEALED.
    """
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.OPEN:
        return False
    if rnd.vote_deadline is None or timezone.now() < rnd.vote_deadline:
        return False
    now = timezone.now()
    updated = Round.objects.filter(
        pk=rnd.pk, state=RoundState.OPEN, vote_deadline__lt=now
    ).update(state=RoundState.REVEALED, revealed_at=now)
    if not updated:
        return False
    # Garde le cache en memoire (room.current_round) coherent avec la ligne
    # tout juste ecrite : les appelants relisent l'etat via current_round(room)
    # (build_state_sync, revealed_payload...) sans recharger depuis la base.
    rnd.state = RoundState.REVEALED
    rnd.revealed_at = now
    room.touch()
    return True


def act_result(room, participant, chosen_value):
    _require_facilitator(room, participant, "result.act")
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.REVEALED:
        raise RoomError("state.invalid_transition", "Not revealed", "result.act")
    if chosen_value not in _card_values(room):
        raise RoomError("state.invalid_transition", "Unknown card value", "result.act")
    item = _first_item(rnd)
    # Result.subject reste NOT NULL en base (aucune migration dans cette tache) :
    # un round issu du nouveau flux n'a plus de Subject (rnd.subject est None).
    # `history.api_views` lit encore `r.subject.text`, donc on en cree un minimal a
    # la volee plutot que de laisser tomber cette contrainte. Le lecteur reel est
    # desormais `item` ; `subject` est un doublon transitoire, comme le reste du
    # design (§5) jusqu'a ce qu'une migration retire la colonne -- ce bloc entier
    # (la creation de secours ET l'ecriture sur rnd.subject ci-dessous) disparait
    # alors avec elle.
    #
    # On memorise la ligne creee sur `rnd.subject` : `select_round` remet un round
    # ACTED a IDLE sans jamais toucher au subject (le nouveau flux ne le renseigne
    # plus), donc un round rejoue plusieurs fois doit retomber sur LE MEME Subject
    # de secours plutot que d'en semer un nouveau a chaque acte -- sans quoi celui
    # du Result precedent perd sa derniere reference des le suivant.
    subject = rnd.subject
    if subject is None:
        subject = Subject.objects.create(
            room=room, text=item.text if item else "", sequence=room.subjects.count() + 1
        )
        rnd.subject = subject
        rnd.save(update_fields=["subject"])
    Result.objects.update_or_create(
        round=rnd,
        defaults={"subject": subject, "item": item, "chosen_value": chosen_value, "decided_by": participant},
    )
    rnd.state = RoundState.ACTED
    rnd.save(update_fields=["state"])
    room.touch()
    return chosen_value


def reset_round(room, participant):
    _require_facilitator(room, participant, "vote.reset")
    rnd = current_round(room)
    if rnd is None:
        raise RoomError("state.invalid_transition", "No round", "vote.reset")
    rnd.votes.all().delete()
    rnd.state = RoundState.IDLE
    rnd.deck_snapshot = None
    rnd.opened_at = None
    rnd.revealed_at = None
    rnd.vote_deadline = None
    rnd.save(update_fields=["deck_snapshot", "state", "opened_at", "revealed_at", "vote_deadline"])
    room.touch()
    return "idle"


def open_deadline(room):
    """Echeance (datetime) du round OPEN courant, ou None.

    Ne renvoie une valeur que pour un round OPEN : meme si une echeance perimee
    traine en base (bug de reinitialisation, etc.), aucun round IDLE/REVEALED/ACTED
    ne peut la divulguer (defense en profondeur). Sert de base a deadline_iso() et,
    cote consumer, a decider s'il faut reprogrammer une tache de revelation a la
    reconnexion (une tache en memoire ne survit pas a un redemarrage du service,
    l'echeance en base si)."""
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.OPEN or rnd.vote_deadline is None:
        return None
    return rnd.vote_deadline


def deadline_iso(room):
    """Echeance du round courant au format ISO, ou None. Sert aux payloads WS."""
    deadline = open_deadline(room)
    return deadline.isoformat() if deadline is not None else None


def participation(room):
    rnd = current_round(room)
    total = room.participants.count()
    if rnd is None:
        return {"voted": 0, "total": total, "votedIds": []}
    voted_ids = list(
        Vote.objects.filter(round=rnd).values_list("participant__public_id", flat=True)
    )
    return {"voted": len(voted_ids), "total": total, "votedIds": [str(pid) for pid in voted_ids]}


def revealed_payload(room):
    """Resultat d'un round revele — ONLY ever called in REVEALED state.

    Deux modes, choisis par le facilitateur a l'ouverture (``rnd.is_anonymous``) :

    - **nominatif** (defaut) : le decompte + la liste participant -> carte ;
    - **anonyme** (option des equipes payantes) : le decompte SEUL. La cle ``votes``
      n'est alors pas emise du tout — masquer cote client serait de la facade, une
      trame WS etant lisible dans les outils de developpement du navigateur.

    Le mode est fige a l'ouverture et annonce aux votants avant qu'ils votent : le
    basculer une fois les votes emis exposerait des gens qui se croyaient anonymes.
    """
    rnd = current_round(room)
    votes = list(Vote.objects.filter(round=rnd))
    counts = Counter(v.card_value for v in votes)
    tally = [
        {"cardValue": value, "count": counts[value]}
        for value in _card_values(room)
        if counts.get(value)
    ]
    spread = _spread_for(_resolution_strategy(room), [v.card_value for v in votes])
    payload = {"tally": tally, "spread": spread, "anonymous": bool(rnd and rnd.is_anonymous)}
    if not (rnd and rnd.is_anonymous):
        by_participant = {v.participant_id: v.card_value for v in votes}
        payload["votes"] = [
            {"participantId": str(p.public_id), "cardValue": by_participant[p.id]}
            for p in room.participants.all()
            if p.id in by_participant
        ]
    return payload


def participants_list(room):
    rnd = current_round(room)
    voted = set()
    if rnd:
        voted = set(
            Vote.objects.filter(round=rnd).values_list("participant_id", flat=True)
        )
    out = []
    for p in room.participants.all():
        out.append(
            {
                "participantId": str(p.public_id),
                "username": p.display_name,
                "role": p.role,
                "hasVoted": p.id in voted,
            }
        )
    return out


def _facilitator_participant(room):
    rnd = current_round(room)
    if rnd and rnd.facilitator_id:
        return room.participants.filter(id=rnd.facilitator_id).first()
    return room.participants.filter(role=Role.FACILITATOR).first()


def facilitator_present(room):
    fac = _facilitator_participant(room)
    return bool(fac and fac.is_connected)


def can_claim(room):
    """Guard (contract §6.f): the takeover opens only once the facilitator has been
    absent for FACILITATOR_GUARD_SECONDS. Enforced at claim-time (no background timer),
    so a brief network blip within the grace period does NOT cost the facilitator the role."""
    fac = _facilitator_participant(room)
    if fac is None:
        return True
    if fac.is_connected:
        return False
    return (timezone.now() - fac.last_seen_at).total_seconds() >= settings.FACILITATOR_GUARD_SECONDS


def promote_facilitator(room, participant):
    """First claimer becomes facilitator. Authority is by rnd.facilitator, so the
    old facilitator returning is a plain voter (definitive transfer, §6.f) — no token
    reissue needed since control is keyed on participant identity, not a secret."""
    rnd = current_round(room)
    if rnd:
        rnd.facilitator = participant
        rnd.save(update_fields=["facilitator"])
    participant.role = Role.FACILITATOR
    participant.save(update_fields=["role"])


def transfer_facilitator(room, participant, target_public_id):
    """Voluntary hand-over (contract §9, Phase 2): the current facilitator gives the
    role to another present participant and becomes a voter."""
    _require_facilitator(room, participant, "facilitator.transfer")
    target = room.participants.filter(public_id=target_public_id).first()
    if target is None or target.id == participant.id:
        raise RoomError("state.invalid_transition", "Unknown or self target", "facilitator.transfer")
    rnd = current_round(room)
    if rnd:
        rnd.facilitator = target
        rnd.save(update_fields=["facilitator"])
    target.role = Role.FACILITATOR
    target.save(update_fields=["role"])
    participant.role = Role.VOTER
    participant.save(update_fields=["role"])
    return str(target.public_id)


def active_deck_snapshot(room):
    """The deck in play: the current round's frozen one, else the room's active one."""
    rnd = room.current_round
    if rnd is not None and rnd.deck_snapshot:
        return rnd.deck_snapshot
    return room.deck_snapshot


def available_decks_payload(room):
    """The room's frozen deck catalogue, light enough for a picker (no cards)."""
    snapshots = room.deck_snapshots or [room.deck_snapshot]
    return [
        {"deckId": s.get("deckId"), "voteType": s.get("voteType"), "cardBack": s.get("cardBack")}
        for s in snapshots
        if s
    ]


def set_reveal_mode(room, participant, anonymous):
    """Choose how the next reveal shows the votes (facilitator only).

    Anonymous reveal is a paid-team option; a free/anonymous room stays nominative.
    Only settable while the round is IDLE: voters are told the mode before voting,
    so flipping it under cast votes would expose people who believed otherwise.
    """
    _require_facilitator(room, participant, "reveal.setMode")
    anonymous = bool(anonymous)
    if anonymous and not _team_may_anonymise(room):
        raise RoomError("forbidden.subscription_required", "Anonymous reveal requires a subscription", "reveal.setMode")
    rnd = current_round(room)
    if rnd is None:
        raise RoomError("state.invalid_transition", "No round", "reveal.setMode")
    if rnd.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Set the mode before opening the vote", "reveal.setMode")
    rnd.is_anonymous = anonymous
    rnd.save(update_fields=["is_anonymous"])
    room.touch()
    return anonymous


def _team_may_anonymise(room):
    if room.team_id is None:
        return False
    from billing.service import team_is_paid

    return team_is_paid(room.team)


def select_deck(room, participant, deck_id):
    """Switch the room's active deck (facilitator only).

    Refused while a round is in flight (OPEN/REVEALED): votes already cast
    reference the current deck's values. A round that is IDLE or already ACTED is
    safe — a played round keeps its own frozen deck either way.
    """
    _require_facilitator(room, participant, "deck.select")
    rnd = current_round(room)
    if rnd is not None and rnd.state in (RoundState.OPEN, RoundState.REVEALED):
        raise RoomError("state.invalid_transition", "Finish the round before switching deck", "deck.select")
    snapshots = room.deck_snapshots or [room.deck_snapshot]
    chosen = next((s for s in snapshots if s and s.get("deckId") == deck_id), None)
    if chosen is None:
        raise RoomError("state.invalid_transition", "Deck not available in this room", "deck.select")
    room.deck_snapshot = chosen
    room.save(update_fields=["deck_snapshot"])
    room.touch()
    return chosen


def build_state_sync(participant):
    """Full current-state snapshot for a single client (contract §5.1). No history replay."""
    room = participant.room
    rnd = current_round(room)
    my_vote = None
    result = None
    round_state = RoundState.IDLE
    subject_text = current_item_text(room)
    if rnd:
        round_state = rnd.state
        vote = Vote.objects.filter(round=rnd, participant=participant).first()
        my_vote = vote.card_value if vote else None
        if rnd.state == RoundState.ACTED and hasattr(rnd, "result"):
            result = rnd.result.chosen_value

    payload = {
        # isTeam drives client-side feature gating (e.g. the timer control is
        # team-only); the server stays authoritative either way.
        "room": {"code": room.code, "title": room.title, "isTeam": room.team_id is not None},
        "protocolVersion": 1,
        "roundState": round_state,
        "subject": subject_text,
        "deckSnapshot": active_deck_snapshot(room),
        "availableDecks": available_decks_payload(room),
        "participants": participants_list(room),
        # Le role du destinataire, et son identifiant pour qu'il se reconnaisse dans
        # les diffusions. Le client le tenait jusqu'ici de l'etat enregistre a
        # l'arrivee, donc fige : prendre le role de facilitateur le rendait tel pour
        # tout le monde SAUF pour lui, et recharger n'y changeait rien, le role perime
        # etant persiste. C'est le serveur qui fait autorite.
        "myRole": participant.role,
        "myParticipantId": str(participant.public_id),
        # Mise en page du depouillement, figee sur la salle : le client remplace sa
        # main par l'une ou l'autre forme des la revelation.
        "resultLayout": room.result_layout,
        "myVote": my_vote,
        "result": result,
        "facilitatorPresent": facilitator_present(room),
        "agenda": build_agenda(room),
        # Les items du round courant. `subject` reste emis a cote, en doublon
        # deprecie, le temps que Facilitation_frontend bascule (design §5) : les
        # alias meurent en 5b, pas avant.
        "items": items_payload(rnd),
        "round": {"id": rnd.id if rnd else None, "state": round_state},
        "deadline": deadline_iso(room),
        "timer": {"enabled": room.timer_enabled, "seconds": room.timer_seconds},
        # Announced to everyone, not just the facilitator: a voter must know whether
        # their card will be shown with their name before they play it.
        "reveal": {
            "anonymous": bool(rnd and rnd.is_anonymous),
            "canAnonymise": _team_may_anonymise(room),
        },
    }
    # Un arrivant — ou un client qui recharge — sur un round deja revele doit voir le
    # MEME resultat que ceux qui etaient la (contrat §5.1, §6.e). On reutilise donc
    # exactement le payload de la revelation, plutot que le seul decompte : il porte
    # aussi l'ecart et, en mode nominatif, le lien participant -> carte qui fait
    # retourner les cartes du tapis. Sans lui, recharger laissait les cartes face
    # cachee alors que le decompte, lui, s'affichait.
    #
    # L'invariant d'anonymat est preserve sans effort : ``revealed_payload`` n'emet
    # aucune cle ``votes`` sur un round anonyme, et c'est bien lui qui decide ici.
    #
    # ACTED est inclus car le client traite « revele » et « acte » comme un seul etat
    # d'affichage : l'omettre laissait le meme trou apres la globalisation.
    if round_state in (RoundState.REVEALED, RoundState.ACTED):
        payload.update(
            {k: v for k, v in revealed_payload(room).items() if k in ("tally", "spread", "votes")}
        )
    return payload


# Alias herites, le temps que le consumer bascule (tache 4). A supprimer ensuite.
set_subject = set_current_item
add_subject = add_scenario_item
select_subject = select_round
current_subject_text = current_item_text
# Idem pour les tests existants qui appelaient encore le nom prive avant que
# `current_round` ne devienne public (tache 3). A supprimer avec le nettoyage
# de ces tests.
_current_round = current_round
