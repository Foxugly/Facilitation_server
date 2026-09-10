"""Synchronous domain logic for the realtime room (data-model spec §5.4, contract §4-§6).

Kept sync + framework-light so it is unit-testable without a socket; the consumer
wraps these with ``database_sync_to_async``. Server is the source of truth: every
mutation validates the state machine and raises ``RoomError`` on an illegal move
(contract §0.1, §6.b) rather than applying it.
"""
from collections import Counter

from django.conf import settings
from django.utils import timezone

from rooms.models import (
    Participant,
    Result,
    Role,
    Room,
    RoundState,
    Subject,
    Vote,
    VoteSession,
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
    session = room.current_session
    snapshot = (session.deck_snapshot if session and session.deck_snapshot else room.deck_snapshot)
    return [card["value"] for card in (snapshot or {}).get("cards", [])]


# Principe P1 de la spec de modele de donnees : la DB decrit un type de vote, le code
# decide du comportement. Seules ces strategies ont une echelle ordinale sur laquelle
# un ecart min/max veut dire quelque chose.
ORDINAL_RESOLUTION_STRATEGIES = frozenset({"delegation_v1", "fist_of_five_v1"})


def _resolution_strategy(room):
    """La strategie du deck actif — session en cours si elle porte un snapshot, sinon
    celui de la salle. Meme regle de priorite que ``_card_values``."""
    session = room.current_session
    snapshot = (session.deck_snapshot if session and session.deck_snapshot else room.deck_snapshot)
    return (snapshot or {}).get("resolutionStrategy", "")


def _spread_for(strategy, card_values):
    """Ecart min/max des votes, ou {None, None} si l'echelle n'est pas ordinale.

    Sans ce garde-fou, un vote romain calculerait son ecart sur les seules valeurs
    passant isdigit() : « +1 » et « -1 » echouent, « 0 » reussit, et l'ecran
    afficherait « 0 - 0 » — un faux consensus — sous un vote pourtant partage.
    """
    if strategy not in ORDINAL_RESOLUTION_STRATEGIES:
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


def _current_session(room):
    return room.current_session


def _require_facilitator(room, participant, rejected_type):
    session = _current_session(room)
    # Authority is the session facilitator; before any session exists, the room's
    # sole facilitator participant holds it (contract §2).
    if session and session.facilitator_id:
        if participant.id != session.facilitator_id:
            raise RoomError("forbidden.not_facilitator", "Not the facilitator", rejected_type)
    elif participant.role != Role.FACILITATOR:
        raise RoomError("forbidden.not_facilitator", "Not the facilitator", rejected_type)


def touch(room):
    room.touch()


def set_subject(room, participant, text):
    _require_facilitator(room, participant, "subject.set")
    session = _current_session(room)
    if session and session.state == RoundState.IDLE:
        session.subject.text = text
        session.subject.save(update_fields=["text"])
    else:
        seq = room.subjects.count() + 1
        subject = Subject.objects.create(room=room, text=text, sequence=seq)
        session = VoteSession.objects.create(
            room=room, subject=subject, state=RoundState.IDLE, facilitator=participant
        )
        room.current_session = session
        room.save(update_fields=["current_session"])
    room.touch()
    return text


def current_subject_text(room):
    s = _current_session(room)
    return s.subject.text if s else ""


def build_agenda(room):
    """The scenario: every subject of the room with its status (current / done / pending)
    and, when acted, its retained value."""
    current_id = room.current_session.subject_id if room.current_session_id else None
    out = []
    for s in room.subjects.all().order_by("sequence").prefetch_related("sessions__result"):
        result = None
        acted = next((se for se in s.sessions.all() if se.state == RoundState.ACTED and hasattr(se, "result")), None)
        if acted:
            result = acted.result.chosen_value
        status = "current" if s.id == current_id else ("done" if result is not None else "pending")
        out.append({"id": s.id, "text": s.text, "status": status, "result": result})
    return out


def add_subject(room, participant, text):
    """Add a subject to the scenario. The first one auto-becomes the current vote."""
    _require_facilitator(room, participant, "subject.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty subject", "subject.add")
    seq = room.subjects.count() + 1
    subject = Subject.objects.create(room=room, text=text, sequence=seq)
    if room.current_session_id is None:
        session = VoteSession.objects.create(room=room, subject=subject, state=RoundState.IDLE, facilitator=participant)
        room.current_session = session
        room.save(update_fields=["current_session"])
    room.touch()
    return subject.id


def select_subject(room, participant, subject_id):
    """Pick a scenario subject to vote on next → resets the round to idle for it."""
    _require_facilitator(room, participant, "subject.select")
    subject = room.subjects.filter(id=subject_id).first()
    if subject is None:
        raise RoomError("state.invalid_transition", "Unknown subject", "subject.select")
    session = subject.sessions.exclude(state=RoundState.ACTED).first()
    if session is None:
        session = VoteSession.objects.create(room=room, subject=subject, state=RoundState.IDLE, facilitator=participant)
    else:
        session.state = RoundState.IDLE
        session.opened_at = None
        session.revealed_at = None
        session.vote_deadline = None
        session.facilitator = participant
        session.save(update_fields=["state", "opened_at", "revealed_at", "vote_deadline", "facilitator"])
        session.votes.all().delete()
    room.current_session = session
    room.save(update_fields=["current_session"])
    room.touch()
    return subject.text


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
    session provably exists and is idle, so none of them can race or reject (that's
    what used to make toggling the reveal mode before any subject existed pop an
    error). Reuses the single-setting services so the rules stay in one place.
    """
    _require_facilitator(room, participant, "round.prepare")
    # 1) Make the chosen subject the current one (creating/resetting its session).
    if subject_id is not None:
        select_subject(room, participant, subject_id)
    elif subject_text is not None and subject_text.strip():
        set_subject(room, participant, subject_text.strip())
    session = _current_session(room)
    if session is None or not session.subject.text.strip():
        raise RoomError("state.invalid_transition", "No subject set", "round.prepare")
    if session.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Round already started", "round.prepare")
    # 2) Details. Each helper re-checks facilitator/state; order is irrelevant now
    #    that the idle session exists.
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
    session.refresh_from_db(fields=["is_anonymous"])
    return {
        "subject": session.subject.text,
        "deckSnapshot": active_deck_snapshot(room),
        "anonymous": bool(session.is_anonymous),
        "timerEnabled": room.timer_enabled,
        "timerSeconds": room.timer_seconds,
    }


def open_vote(room, participant):
    _require_facilitator(room, participant, "vote.open")
    session = _current_session(room)
    if session is None or not session.subject.text.strip():
        raise RoomError("state.invalid_transition", "No subject set", "vote.open")
    if session.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Not idle", "vote.open")
    # Freeze the deck this round is played with: the room's active deck may change
    # afterwards, and the round's values must keep their meaning (history labels).
    session.deck_snapshot = room.deck_snapshot
    session.state = RoundState.OPEN
    session.opened_at = timezone.now()
    session.vote_deadline = (
        session.opened_at + timezone.timedelta(seconds=room.timer_seconds)
        if room.timer_enabled
        else None
    )
    session.save(update_fields=["deck_snapshot", "state", "opened_at", "vote_deadline"])
    room.touch()
    return session.vote_deadline


def cast_vote(room, participant, card_value):
    session = _current_session(room)
    if session is None or session.state != RoundState.OPEN:
        raise RoomError("state.invalid_transition", "Voting is not open", "vote.cast")
    if session.vote_deadline is not None and timezone.now() > session.vote_deadline:
        raise RoomError("state.invalid_transition", "Voting time is over", "vote.cast")
    if card_value not in _card_values(room):
        raise RoomError("state.invalid_transition", "Unknown card value", "vote.cast")
    Vote.objects.update_or_create(
        session=session, participant=participant, defaults={"card_value": card_value}
    )
    room.touch()


def reveal(room, participant):
    _require_facilitator(room, participant, "vote.reveal")
    session = _current_session(room)
    if session is None or session.state != RoundState.OPEN:
        raise RoomError("state.invalid_transition", "Not open", "vote.reveal")
    if not session.votes.exists():
        raise RoomError("state.invalid_transition", "No votes yet", "vote.reveal")
    session.state = RoundState.REVEALED
    session.revealed_at = timezone.now()
    session.save(update_fields=["state", "revealed_at"])
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
    session = _current_session(room)
    if session is None or session.state != RoundState.OPEN:
        return False
    if session.vote_deadline is None or timezone.now() < session.vote_deadline:
        return False
    now = timezone.now()
    updated = VoteSession.objects.filter(
        pk=session.pk, state=RoundState.OPEN, vote_deadline__lt=now
    ).update(state=RoundState.REVEALED, revealed_at=now)
    if not updated:
        return False
    # Garde le cache en memoire (room.current_session) coherent avec la ligne
    # tout juste ecrite : les appelants relisent l'etat via _current_session(room)
    # (build_state_sync, revealed_payload...) sans recharger depuis la base.
    session.state = RoundState.REVEALED
    session.revealed_at = now
    room.touch()
    return True


def act_result(room, participant, chosen_value):
    _require_facilitator(room, participant, "result.act")
    session = _current_session(room)
    if session is None or session.state != RoundState.REVEALED:
        raise RoomError("state.invalid_transition", "Not revealed", "result.act")
    if chosen_value not in _card_values(room):
        raise RoomError("state.invalid_transition", "Unknown card value", "result.act")
    Result.objects.update_or_create(
        session=session,
        defaults={"subject": session.subject, "chosen_value": chosen_value, "decided_by": participant},
    )
    session.state = RoundState.ACTED
    session.save(update_fields=["state"])
    room.touch()
    return chosen_value


def reset_round(room, participant):
    _require_facilitator(room, participant, "vote.reset")
    session = _current_session(room)
    if session is None:
        raise RoomError("state.invalid_transition", "No round", "vote.reset")
    session.votes.all().delete()
    session.state = RoundState.IDLE
    session.deck_snapshot = None
    session.opened_at = None
    session.revealed_at = None
    session.vote_deadline = None
    session.save(update_fields=["deck_snapshot", "state", "opened_at", "revealed_at", "vote_deadline"])
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
    session = _current_session(room)
    if session is None or session.state != RoundState.OPEN or session.vote_deadline is None:
        return None
    return session.vote_deadline


def deadline_iso(room):
    """Echeance du round courant au format ISO, ou None. Sert aux payloads WS."""
    deadline = open_deadline(room)
    return deadline.isoformat() if deadline is not None else None


def participation(room):
    session = _current_session(room)
    total = room.participants.count()
    if session is None:
        return {"voted": 0, "total": total, "votedIds": []}
    voted_ids = list(
        Vote.objects.filter(session=session).values_list("participant__public_id", flat=True)
    )
    return {"voted": len(voted_ids), "total": total, "votedIds": [str(pid) for pid in voted_ids]}


def revealed_payload(room):
    """Resultat d'un round revele — ONLY ever called in REVEALED state.

    Deux modes, choisis par le facilitateur a l'ouverture (``session.is_anonymous``) :

    - **nominatif** (defaut) : le decompte + la liste participant -> carte ;
    - **anonyme** (option des equipes payantes) : le decompte SEUL. La cle ``votes``
      n'est alors pas emise du tout — masquer cote client serait de la facade, une
      trame WS etant lisible dans les outils de developpement du navigateur.

    Le mode est fige a l'ouverture et annonce aux votants avant qu'ils votent : le
    basculer une fois les votes emis exposerait des gens qui se croyaient anonymes.
    """
    session = _current_session(room)
    votes = list(Vote.objects.filter(session=session))
    counts = Counter(v.card_value for v in votes)
    tally = [
        {"cardValue": value, "count": counts[value]}
        for value in _card_values(room)
        if counts.get(value)
    ]
    spread = _spread_for(_resolution_strategy(room), [v.card_value for v in votes])
    payload = {"tally": tally, "spread": spread, "anonymous": bool(session and session.is_anonymous)}
    if not (session and session.is_anonymous):
        by_participant = {v.participant_id: v.card_value for v in votes}
        payload["votes"] = [
            {"participantId": str(p.public_id), "cardValue": by_participant[p.id]}
            for p in room.participants.all()
            if p.id in by_participant
        ]
    return payload


def participants_list(room):
    session = _current_session(room)
    voted = set()
    if session:
        voted = set(
            Vote.objects.filter(session=session).values_list("participant_id", flat=True)
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
    session = _current_session(room)
    if session and session.facilitator_id:
        return room.participants.filter(id=session.facilitator_id).first()
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
    """First claimer becomes facilitator. Authority is by session.facilitator, so the
    old facilitator returning is a plain voter (definitive transfer, §6.f) — no token
    reissue needed since control is keyed on participant identity, not a secret."""
    session = _current_session(room)
    if session:
        session.facilitator = participant
        session.save(update_fields=["facilitator"])
    participant.role = Role.FACILITATOR
    participant.save(update_fields=["role"])


def transfer_facilitator(room, participant, target_public_id):
    """Voluntary hand-over (contract §9, Phase 2): the current facilitator gives the
    role to another present participant and becomes a voter."""
    _require_facilitator(room, participant, "facilitator.transfer")
    target = room.participants.filter(public_id=target_public_id).first()
    if target is None or target.id == participant.id:
        raise RoomError("state.invalid_transition", "Unknown or self target", "facilitator.transfer")
    session = _current_session(room)
    if session:
        session.facilitator = target
        session.save(update_fields=["facilitator"])
    target.role = Role.FACILITATOR
    target.save(update_fields=["role"])
    participant.role = Role.VOTER
    participant.save(update_fields=["role"])
    return str(target.public_id)


def active_deck_snapshot(room):
    """The deck in play: the current round's frozen one, else the room's active one."""
    session = room.current_session
    if session is not None and session.deck_snapshot:
        return session.deck_snapshot
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
    session = _current_session(room)
    if session is None:
        raise RoomError("state.invalid_transition", "No round", "reveal.setMode")
    if session.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Set the mode before opening the vote", "reveal.setMode")
    session.is_anonymous = anonymous
    session.save(update_fields=["is_anonymous"])
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
    session = _current_session(room)
    if session is not None and session.state in (RoundState.OPEN, RoundState.REVEALED):
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
    session = _current_session(room)
    my_vote = None
    result = None
    round_state = RoundState.IDLE
    subject_text = ""
    if session:
        round_state = session.state
        subject_text = session.subject.text
        vote = Vote.objects.filter(session=session, participant=participant).first()
        my_vote = vote.card_value if vote else None
        if session.state == RoundState.ACTED and hasattr(session, "result"):
            result = session.result.chosen_value

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
        # les diffusions. Le client le tenait jusqu'ici de sa session enregistree a
        # l'arrivee, donc figee : prendre le role de facilitateur le rendait tel pour
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
        "deadline": deadline_iso(room),
        "timer": {"enabled": room.timer_enabled, "seconds": room.timer_seconds},
        # Announced to everyone, not just the facilitator: a voter must know whether
        # their card will be shown with their name before they play it.
        "reveal": {
            "anonymous": bool(session and session.is_anonymous),
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
