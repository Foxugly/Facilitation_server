"""Synchronous domain logic for the realtime room (data-model spec §5.4, contract §4-§6).

Kept sync + framework-light so it is unit-testable without a socket; the consumer
wraps these with ``database_sync_to_async``. Server is the source of truth: every
mutation validates the state machine and raises ``RoomError`` on an illegal move
(contract §0.1, §6.b) rather than applying it.
"""
from collections import Counter

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from realtime.activities import RoomError, spec_for, validate_config, validate_payload

from rooms.models import (
    Item,
    Participant,
    Result,
    Role,
    Room,
    RoundState,
    Response,
    Round,
)


TIMER_MIN_SECONDS = 10
TIMER_MAX_SECONDS = 60
TIMER_STEP_SECONDS = 5


# `RoomError` vit desormais dans `realtime.activities` (pour que
# `validate_payload` puisse la lever sans import circulaire) et est reexportee
# ici : le code et les tests existants qui font `from realtime.services import
# RoomError` (`realtime/consumers.py` compris) continuent de fonctionner sans
# modification.


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
    return _round_resolution_strategy(room.current_round, room)


def _round_resolution_strategy(rnd, room):
    """Meme regle que ``_resolution_strategy``, mais pour UN round donne plutot
    que pour le round courant de la room. Necessaire pour `update_item` et
    `remove_item` : l'item vise n'est pas forcement dans le round courant (5c a
    appris que chaque round fige son propre type pour toute sa vie), donc la
    politique `items_authored_by` a verifier est celle du round qui PORTE
    l'item, pas celle du round actif."""
    snapshot = (rnd.deck_snapshot if rnd and rnd.deck_snapshot else room.deck_snapshot)
    return (snapshot or {}).get("resolutionStrategy", "")


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


def _is_facilitator(room, participant):
    # Compare par PK (`participant.id`), donc suppose `participant` persistant
    # (un PK non-None). Vrai de tout `Participant` recu du consumer (resolu par
    # `resolve_participant`, toujours charge depuis la DB) -- mais un futur
    # appelant qui passerait une instance non sauvegardee casserait la garde en
    # silence (`None == None`). `ValueError` et non `assert` : un `assert`
    # disparait en mode optimise (`python -O` / `PYTHONOPTIMIZE`), ce qui
    # ferait disparaitre ce filet sans qu'aucun test (qui ne tourne pas en
    # mode optimise) puisse jamais le detecter. `ValueError`, et non
    # `RoomError`, parce que c'est un bug d'appelant a corriger avant merge,
    # pas une entree utilisateur a refuser proprement.
    if participant.id is None:
        raise ValueError("_is_facilitator: participant non persistant (id is None)")
    rnd = current_round(room)
    # Authority is the round facilitator; before any round exists, the room's
    # sole facilitator participant holds it (contract §2).
    if rnd and rnd.facilitator_id:
        return participant.id == rnd.facilitator_id
    return participant.role == Role.FACILITATOR


def _require_facilitator(room, participant, rejected_type):
    if not _is_facilitator(room, participant):
        raise RoomError("forbidden.not_facilitator", "Not the facilitator", rejected_type)


def _require_item_author(room, participant, item, rejected_type):
    """Garde de `update_item`/`remove_item` quand l'activite du round PORTEUR
    de l'item declare `items_authored_by = "participants"` : le facilitateur
    peut toujours agir, quel que soit l'auteur (design §5) ; un autre
    participant ne peut agir que sur SON PROPRE item.

    `item.author_id` est `None` quand l'auteur a quitte la salle (`Item.author`
    est `SET_NULL`) ou quand le facilitateur a pose l'item au nom de la room :
    dans les deux cas `participant.id != None` est toujours vrai, donc un item
    sans auteur ne redevient modifiable par AUCUN participant ordinaire — une
    regle deliberee, pas un hasard du SET_NULL : un depart de salle ne doit pas
    se traduire par une ouverture de l'ecriture a tous.

    Deux refus distincts, deux codes distincts (correction ronde 1) : sous une
    activite facilitateur-seul, un participant ordinaire n'a jamais pretendu
    faciliter -- `forbidden.not_facilitator` reste exact. Sous une activite
    "participants", il EST autorise a creer/editer/supprimer, juste pas CET
    item -- le confondre avec un refus d'autorite (`forbidden.not_facilitator`,
    message « Not the facilitator ») serait factuellement faux et
    indistinguable, cote front, d'un vrai refus de role.
    """
    if _is_facilitator(room, participant):
        return
    strategy = _round_resolution_strategy(item.round, room)
    if spec_for(strategy).items_authored_by == "participants":
        if item.author_id == participant.id:
            return
        raise RoomError("forbidden.not_item_author", "Not this item's author", rejected_type)
    raise RoomError("forbidden.not_facilitator", "Not the facilitator", rejected_type)


def touch(room):
    room.touch()


def items_payload(rnd):
    """Les items d'un round, dans l'ordre du facilitateur."""
    if rnd is None:
        return []
    return [{"id": i.id, "text": i.text, "sequence": i.sequence} for i in rnd.items.all()]


def _next_round_sequence(room):
    """Sequence a attribuer au PROCHAIN round de la salle : le maximum existant
    + 1, plus jamais `room.rounds.count() + 1` (tache 2). Le compte retombe
    quand un round est retire (3 rounds -> 2), donc `count() + 1` pouvait
    REDONNER une sequence deja portee par un round restant -- c'est exactement
    le piege documente par
    `test_removing_a_round_leaves_a_gap_but_next_round_does_not_collide`
    (`rooms/tests/test_round_sequence.py`). Le maximum, lui, ne redescend
    jamais apres un retrait : aucune sequence future ne peut retomber sur une
    sequence existante."""
    maximum = room.rounds.aggregate(Max("sequence"))["sequence__max"]
    return (maximum or 0) + 1


def _new_round(room, participant, text):
    """Un round neuf portant un premier item. Le scenario est une file de rounds :
    poser un nouveau sujet, c'est ouvrir un round de plus, a la suite de la file
    existante (tache 1) -- jamais a la place d'un round present."""
    rnd = Round.objects.create(
        room=room, state=RoundState.IDLE, facilitator=participant, sequence=_next_round_sequence(room)
    )
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
    """Ajoute un item AU ROUND COURANT — le N-items du design §3.

    Qui a le droit n'est plus code en dur ici : c'est `items_authored_by` du
    registre (design §6) qui le dit — facilitateur seul (poker, defaut), ou
    tout participant (brainstorming). Quand un participant ordinaire pose son
    propre post-it, `Item.author` le porte ; quand c'est le facilitateur qui
    pose un sujet au nom de la room, `author` reste `None`, comme aujourd'hui.
    """
    is_facilitator = _is_facilitator(room, participant)
    strategy = _resolution_strategy(room)
    if spec_for(strategy).items_authored_by != "participants" and not is_facilitator:
        raise RoomError("forbidden.not_facilitator", "Not the facilitator", "item.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.add")
    author = None if is_facilitator else participant
    rnd = current_round(room)
    if rnd is None:
        # Aucun round courant : seul le facilitateur peut en ouvrir un
        # (`_new_round` lui assigne la facilitation du round neuf). Un
        # participant ordinaire sous la politique "participants" n'a, lui,
        # aucun round ou ecrire son post-it tant que le facilitateur n'a pas
        # prepare le round — pas de round fantome dont il deviendrait
        # facilitateur.
        if not is_facilitator:
            raise RoomError("state.invalid_transition", "No active round", "item.add")
        rnd = _new_round(room, participant, text)
        room.current_round = rnd
        room.save(update_fields=["current_round"])
        item = _first_item(rnd)
    else:
        seq = rnd.items.count() + 1
        item = Item.objects.create(round=rnd, text=text, sequence=seq, author=author)
    room.touch()
    return {"id": item.id, "text": item.text, "sequence": item.sequence}


def _item_of_room(room, item_id, rejected_type):
    item = Item.objects.filter(id=item_id, round__room=room).select_related("round").first()
    if item is None:
        raise RoomError("state.invalid_transition", "Unknown item", rejected_type)
    return item


def update_item(room, participant, item_id, text):
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "item.update")
    item = _item_of_room(room, item_id, "item.update")
    _require_item_author(room, participant, item, "item.update")
    # Meme garde que remove_item, et pour la meme raison : `history` affiche
    # `Result.item.text` (history/api_views.py), donc reformuler un item deja acte
    # reecrirait retroactivement le rapport deja envoye aux managers.
    if item.results.exists():
        raise RoomError("state.invalid_transition", "Item already decided", "item.update")
    item.text = text
    item.save(update_fields=["text"])
    room.touch()
    return {"id": item.id, "text": item.text, "sequence": item.sequence}


def remove_item(room, participant, item_id):
    item = _item_of_room(room, item_id, "item.remove")
    _require_item_author(room, participant, item, "item.remove")
    # Un round en vol (open/revealed/acted) ne perd pas ses items : les votes deja
    # emis les designent, et `act_result` se retrouverait sans item ou accrocher son
    # resultat — `Result.item` est NOT NULL, l'IntegrityError qui s'ensuivait
    # n'etait pas un RoomError et fermait la socket du facilitateur.
    if item.round.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Round already started", "item.remove")
    # Un item deja acte porte un resultat fige : le retirer reecrirait
    # l'historique, que le design interdit explicitement. Garde distincte de la
    # precedente : un round acte puis reinitialise est IDLE et garde son Result.
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
    # Arbitrage ronde 1 : `reorder_items` reste FACILITATEUR SEUL, meme sous
    # `items_authored_by = "participants"` -- volontairement non couvert par
    # `_require_item_author`. Creer/editer/supprimer portent sur la
    # contribution PROPRE d'un participant ; reordonner porte sur la LISTE
    # ENTIERE du round, items des autres compris -- c'est un geste de
    # facilitation (ranger un tableau), pas un droit d'auteur. Laisser un
    # participant reordonner lui permettrait de faire remonter son propre
    # post-it en deplacant ceux des autres, ce que l'activite ne doit pas
    # autoriser. Voir le test
    # `test_a_voter_cannot_reorder_items_even_when_they_may_add_their_own`.
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
    quel.
    """
    current_id = room.current_round_id
    out = []
    for rnd in room.rounds.all().order_by("sequence", "id").prefetch_related("items", "results"):
        first = rnd.items.first()
        # Le filtre d'etat n'est pas decoratif : `vote.reset` remet le round a IDLE
        # en LAISSANT son Result en place. Sans lui, un round reinitialise
        # reapparaitrait « done », avec l'ancienne valeur, alors qu'il est a rejouer.
        acted = rnd.results.first() if rnd.state == RoundState.ACTED else None
        result = acted.chosen_value if acted else None
        status = "current" if rnd.id == current_id else ("done" if result is not None else "pending")
        out.append({
            "id": rnd.id,
            "text": first.text if first else "",
            "status": status,
            "result": result,
            "items": items_payload(rnd),
        })
    return out


def _replay_round(room, participant, source):
    """Un round NEUF portant une COPIE des items de `source` (design §4).

    Copie et non reference : le facilitateur doit pouvoir reformuler le sujet du
    nouveau tour sans reecrire l'historique du precedent. `origin_item` remonte a
    l'item d'origine — la premiere copie, comme le fait la migration de donnees —
    et l'auteur suit l'item, un post-it ne perdant pas son auteur en changeant de
    round (design §7).

    Rejouer, c'est la MEME activite avec des reponses neuves : le deck (donc le
    TYPE) et la config suivent, comme les items. Sans ca, un round dont le deck
    actif de la room a change de type entretemps (poker -> dot voting, ou
    l'inverse) reviendrait rejoue dans un AUTRE type que celui qui a produit ses
    items et son Result d'origine. Si la source n'a jamais fige de deck (prepare
    sans choix explicite, jamais ouverte), `source.deck_snapshot` est deja None :
    on ne fige alors rien de plus que ce que la source avait elle-meme, et le
    round neuf herite du deck actif a l'ouverture, comme aujourd'hui.

    La SEQUENCE, elle, ne suit PAS la source (tache 1) : un rejeu se place a la
    fin de la file de la salle, il ne s'insere pas a la place du round qu'il
    rejoue. `_next_round_sequence` (maximum existant + 1) est ce qui le
    garantit meme quand la source n'est plus le dernier round de la file --
    voir `test_replaying_a_round_that_is_not_last_appends_at_the_end`.
    """
    rnd = Round.objects.create(
        room=room,
        state=RoundState.IDLE,
        facilitator=participant,
        sequence=_next_round_sequence(room),
        deck_snapshot=source.deck_snapshot,
        # dict(...) : une copie, pas la meme reference — la source et le rejeu
        # ne doivent jamais partager un objet mutable en memoire.
        config=dict(source.config),
    )
    for item in source.items.all():
        Item.objects.create(
            round=rnd,
            text=item.text,
            sequence=item.sequence,
            author=item.author,
            origin_item=item.origin_item or item,
        )
    return rnd


def select_round(room, participant, round_id):
    """Ex-`select_subject` : reprendre un round du scenario le remet a idle.

    Un round ACTE n'est jamais rejoue EN PLACE : on ouvre un round neuf portant une
    copie de ses items, exactement comme l'ancien `select_subject` creait un round
    de plus des que le precedent etait acte. Le rejouer en place laisserait le deck
    fige du tour precedent (premier vote rejete en « Unknown card value » apres un
    changement de deck), collerait son mode d'anonymat, et ECRASERAIT son Result au
    lieu d'en produire un second.
    """
    _require_facilitator(room, participant, "round.select")
    rnd = room.rounds.filter(id=round_id).first()
    if rnd is None:
        raise RoomError("state.invalid_transition", "Unknown round", "round.select")
    if rnd.state == RoundState.ACTED:
        rnd = _replay_round(room, participant, rnd)
    elif rnd.state != RoundState.IDLE:
        rnd.state = RoundState.IDLE
        rnd.opened_at = None
        rnd.revealed_at = None
        rnd.vote_deadline = None
        rnd.facilitator = participant
        rnd.save(update_fields=["state", "opened_at", "revealed_at", "vote_deadline", "facilitator"])
        rnd.responses.all().delete()
    room.current_round = rnd
    room.save(update_fields=["current_round"])
    room.touch()
    first = rnd.items.first()
    return {"roundId": rnd.id, "items": items_payload(rnd), "text": first.text if first else ""}


def add_scenario_item(room, participant, text):
    """Ex-`add_subject` : ajoute une entree au scenario, donc un ROUND de plus.
    Retourne l'id du round cree — c'est lui que l'agenda designe.
    """
    _require_facilitator(room, participant, "round.add")
    text = (text or "").strip()
    if not text:
        raise RoomError("state.invalid_transition", "Empty item", "round.add")
    rnd = _new_round(room, participant, text)
    if room.current_round_id is None:
        room.current_round = rnd
        room.save(update_fields=["current_round"])
    room.touch()
    return rnd.id


def reorder_rounds(room, participant, round_ids):
    """Refixe la sequence des rounds de la salle sur l'ordre donne (tache 2) :
    c'est le geste qui fait d'une file un scenario compose en amont.

    Facilitateur seul, meme raisonnement que `reorder_items` : reordonner
    porte sur la file ENTIERE, pas sur une contribution propre -- ce n'est pas
    un droit d'auteur, c'est un geste de facilitation.

    Deplacer un round deja ACTE est autorise, y compris celui qui vient
    d'etre acte : la sequence ne pilote que l'AFFICHAGE de l'agenda dans la
    salle, jamais l'historique (`history/`), qui se trie sur `decided_at`, pas
    sur `Round.sequence`. Changer son rang dans la file ne reecrit donc rien
    -- a la difference de `remove_round`, qui protege le `Result` lui-meme.
    """
    _require_facilitator(room, participant, "round.reorder")
    known = {rnd.id: rnd for rnd in room.rounds.all()}
    # len() en plus du set() : sans elle, [1, 1, 2] passe pour {1, 2} -- meme
    # piege que `reorder_items`, deja paye sur les items de ce depot.
    if len(round_ids) != len(known) or set(round_ids) != set(known):
        raise RoomError("state.invalid_transition", "Round set mismatch", "round.reorder")
    for index, round_id in enumerate(round_ids, start=1):
        rnd = known[round_id]
        rnd.sequence = index
        rnd.save(update_fields=["sequence"])
    room.touch()
    return build_agenda(room)


def remove_round(room, participant, round_id):
    """Retire un round du scenario (tache 2). On n'elague que ce qui n'a pas
    encore vecu -- regle resserree au round de correction 1 -- : un round
    n'est retirable que s'il reunit les TROIS conditions ci-dessous. C'est
    explicable en une phrase a un facilitateur : « on ne retire qu'un round
    prepare qui n'est pas a l'ecran ».

    1. Il ne porte aucun `Result` -- l'historique ne se reecrit pas, meme
       garde et meme motif que `remove_item` pour un item deja acte. Un round
       ACTE puis remis a `idle` (`vote.reset`) reste protege : son `Result`
       survit au reset (c'est le but de `vote.reset`), donc cette garde
       continue de le couvrir meme si la garde d'etat ci-dessous, elle, ne le
       verrait plus.
    2. Il est `idle`. Pas seulement "il n'est pas le round courant" : la
       premiere version de cette garde ne testait que le pointeur
       `room.current_round_id`, or `select_round` n'impose pas de fermer un
       round avant d'en designer un autre comme courant -- un round `open`
       avec des reponses vivantes cesse d'etre courant sans jamais etre acte,
       donc sans jamais porter de `Result`. Sans la garde d'ETAT, ce round
       abandonne en vol redevenait retirable des qu'on basculait ailleurs :
       exactement le contournement, en deux gestes au lieu d'un, de la raison
       d'etre de la garde suivante -- un round en cours de vote disparaissait
       alors silencieusement.
    3. Il n'est pas le round COURANT -- choix arrete ici, pas laisse au hasard
       de l'implementation. L'autre lecture possible (accepter et designer un
       autre round comme courant) forcerait un choix arbitraire -- lequel
       devient courant ? le suivant par sequence ? le premier round
       `pending` ? -- au nom du facilitateur, qui n'a pourtant rien demande
       d'autre que "retirer CE round". Pire : un participant connecte regarde
       le round courant en direct (state.sync/agenda) ; le faire basculer
       vers un AUTRE round sans geste explicite du facilitateur serait un
       changement d'activite impose silencieusement sous ses yeux. Refuser
       est sans surprise : le facilitateur choisit explicitement son nouveau
       round courant via `select_round` avant de pouvoir retirer l'ancien.
    """
    _require_facilitator(room, participant, "round.remove")
    rnd = room.rounds.filter(id=round_id).first()
    if rnd is None:
        raise RoomError("state.invalid_transition", "Unknown round", "round.remove")
    if rnd.results.exists():
        raise RoomError("state.invalid_transition", "Round already decided", "round.remove")
    if rnd.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Round in flight", "round.remove")
    if room.current_round_id == rnd.id:
        raise RoomError("state.invalid_transition", "Current round", "round.remove")
    rnd.delete()
    for index, remaining in enumerate(room.rounds.all().order_by("sequence", "id"), start=1):
        if remaining.sequence != index:
            remaining.sequence = index
            remaining.save(update_fields=["sequence"])
    room.touch()
    return round_id


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


@transaction.atomic
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
    call — pick/set the subject, the deck, the reveal mode and the timer — but leave
    it IDLE (not open). Opening is a separate step (``open_vote``).

    This lets the facilitator manipulate the panel as a *form* (subject + details)
    and commit it in one go: every setting is applied while the round provably
    exists and is idle, so none of them can race (that's what used to make toggling
    the reveal mode before any subject existed pop an error). Wrapped in
    ``transaction.atomic`` so this is genuinely atomic: if a later setting is
    rejected (e.g. an anonymous reveal without a subscription), whatever this same
    call already wrote — the deck included — rolls back with it, instead of leaving
    the round half-configured. Reuses the single-setting services so the rules stay
    in one place.
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
        # Fige le deck SUR LE ROUND des la preparation, et non a l'ouverture :
        # sans cela, preparer un second round avec un autre deck reecrirait
        # celui de la room et changerait le type du premier sous les pieds du
        # facilitateur. C'est ce qui rend un scenario multi-activites possible.
        room.refresh_from_db(fields=["deck_snapshot"])
        rnd.deck_snapshot = room.deck_snapshot
        rnd.save(update_fields=["deck_snapshot"])
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


@transaction.atomic
def configure_round(room, participant, round_id, *, deck_id=None, config=None):
    """Fige la config d'un round (et, en option, son deck) AVANT ouverture —
    meme raison que pour le mode d'anonymat (`set_reveal_mode`) : les
    participants doivent savoir a quoi ils jouent avant de jouer.

    Le deck est fige via le MEME chemin que `prepare_round` (task 1) plutot
    que reecrit ici : `select_deck` change le deck ACTIF de la room, puis on
    reporte ce choix sur le round pour qu'un changement ulterieur du deck actif
    ne le lui reecrive pas sous les pieds.

    Enveloppe dans `transaction.atomic` : une configuration refusee par
    `validate_config` ne doit laisser AUCUNE ecriture derriere elle, y compris
    le deck deja applique plus haut dans ce meme appel — sans quoi un coup
    refuse serait quand meme applique pour moitie.
    """
    _require_facilitator(room, participant, "round.configure")
    rnd = room.rounds.filter(id=round_id).first()
    if rnd is None:
        raise RoomError("state.invalid_transition", "Unknown round", "round.configure")
    if rnd.state != RoundState.IDLE:
        raise RoomError("state.invalid_transition", "Round already started", "round.configure")
    if deck_id is not None:
        select_deck(room, participant, deck_id)
        room.refresh_from_db(fields=["deck_snapshot"])
        rnd.deck_snapshot = room.deck_snapshot
        rnd.save(update_fields=["deck_snapshot"])
    if config is not None:
        snapshot = rnd.deck_snapshot if rnd.deck_snapshot else room.deck_snapshot
        strategy = (snapshot or {}).get("resolutionStrategy", "")
        validate_config(strategy, config)
        rnd.config = config
        rnd.save(update_fields=["config"])
    room.touch()
    return {
        "roundId": rnd.id,
        "deckSnapshot": rnd.deck_snapshot if rnd.deck_snapshot else room.deck_snapshot,
        "config": rnd.config,
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
    # Un round prepare avec un deck explicite l'a deja fige (prepare_round) : ne
    # pas l'ecraser ici, sinon preparer un round B reecrirait le type du round A
    # des l'ouverture de A.
    if rnd.deck_snapshot is None:
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


def responses_of(rnd, item):
    """Les reponses d'un item donne (tache 3) : le decompte par item n'a plus
    le droit de lire `rnd.responses` en vrac, un round pouvant en porter
    plusieurs."""
    return list(Response.objects.filter(round=rnd, item=item))


def cast_response(room, participant, item_id, payload):
    """Ecrit la reponse d'un participant a UN item (design section 3).

    Gardes : round ouvert, echeance non depassee, l'item doit appartenir au
    round courant, et le payload doit passer le schema que declare le
    registre pour la strategie active. Chemin unique d'ecriture.
    """
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.OPEN:
        raise RoomError("state.invalid_transition", "Voting is not open", "response.cast")
    if rnd.vote_deadline is not None and timezone.now() > rnd.vote_deadline:
        raise RoomError("state.invalid_transition", "Voting time is over", "response.cast")
    item = rnd.items.filter(id=item_id).first()
    if item is None:
        # Y compris un item d'un AUTRE round : `rnd.items` ne contient que ceux
        # du round courant, un id valide ailleurs n'y figure pas.
        raise RoomError("state.invalid_transition", "Unknown item", "response.cast")
    strategy = _resolution_strategy(room)
    validate_payload(strategy, payload)
    # La regle "la valeur est jouable" vit dans le registre (`validate_value`),
    # pas ici : une activite au payload different de {"card": ...} ne doit pas
    # heriter de la regle "la carte appartient au deck", qui ne la concerne pas.
    if not spec_for(strategy).validate_value(payload, _card_values(room)):
        raise RoomError("state.invalid_transition", "Unknown card value", "response.cast")
    Response.objects.update_or_create(
        item=item,
        participant=participant,
        defaults={"round": rnd, "payload": payload},
    )
    room.touch()
    return payload


def reveal(room, participant):
    _require_facilitator(room, participant, "vote.reveal")
    rnd = current_round(room)
    if rnd is None or rnd.state != RoundState.OPEN:
        raise RoomError("state.invalid_transition", "Not open", "vote.reveal")
    if not rnd.responses.exists():
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
    if item is None:
        # `Result.item` est NOT NULL : sans ce refus, update_or_create leverait une
        # IntegrityError, que le consumer ne rattrape pas (il ne connait que
        # RoomError) et qui coutait sa socket au facilitateur.
        raise RoomError("state.invalid_transition", "No item to act on", "result.act")
    Result.objects.update_or_create(
        round=rnd,
        item=item,
        defaults={"chosen_value": chosen_value, "decided_by": participant},
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
    rnd.responses.all().delete()
    rnd.state = RoundState.IDLE
    # Le deck NE se remet plus a None ici (avant cette tache, ca laissait
    # open_vote le refiger sur le deck ACTIF de la room, seule semantique
    # possible tant qu'un round n'avait pas de deck propre). Depuis que
    # prepare_round fige le deck SUR LE ROUND des la preparation, et que
    # open_vote ne l'ecrase plus s'il est deja fige, effacer ici ferait
    # perdre au round son type au reset : le rouvrir lui donnerait alors le
    # deck ACTIF courant de la room — celui d'un AUTRE round prepare entre
    # temps — au lieu du sien. Un round garde son type pour toute sa vie ;
    # le changer explicitement passe par select_deck / prepare_round.
    rnd.opened_at = None
    rnd.revealed_at = None
    rnd.vote_deadline = None
    rnd.save(update_fields=["state", "opened_at", "revealed_at", "vote_deadline"])
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


def _completed_participant_ids(rnd):
    """Participants ayant repondu a TOUS les items du round (tache 3) : « a
    vote » n'est plus « a repondu a un item », qui laisserait passer pour
    complet un participant a mi-chemin. Tant qu'un round ne porte qu'un seul
    item — le cas du poker aujourd'hui — identique a l'ancien comportement.
    """
    n_items = rnd.items.count()
    if n_items == 0:
        return set()
    counts = Counter(Response.objects.filter(round=rnd).values_list("participant_id", flat=True))
    return {pid for pid, count in counts.items() if count >= n_items}


def participation(room):
    rnd = current_round(room)
    total = room.participants.count()
    if rnd is None:
        return {"voted": 0, "total": total, "votedIds": []}
    complete_ids = _completed_participant_ids(rnd)
    voted_ids = [
        str(pid)
        for pid in room.participants.filter(id__in=complete_ids).values_list(
            "public_id", flat=True
        )
    ]
    return {"voted": len(voted_ids), "total": total, "votedIds": voted_ids}


def revealed_payload(room):
    """Resultat d'un round revele — ONLY ever called in REVEALED state.

    Porte un bloc PAR ITEM (``itemResults``), chacun agrege par l'``aggregate``
    que declare le registre pour la strategie active.

    ``itemResults`` et non ``items`` : ``state.sync`` emet deja une cle
    ``items`` de forme differente (``[{id, text, sequence}]``, la liste des
    items du round). Meme nom, deux formes : un client qui fusionne les deux
    payloads ecraserait silencieusement sa liste d'items avec le decompte.

    Deux modes, choisis par le facilitateur a l'ouverture (``rnd.is_anonymous``) :

    - **nominatif** (defaut) : le decompte + la liste participant -> carte ;
    - **anonyme** (option des equipes payantes) : le decompte SEUL. La cle ``votes``
      n'est alors pas emise du tout — masquer cote client serait de la facade, une
      trame WS etant lisible dans les outils de developpement du navigateur.
      L'invariant tient PAR ITEM : aucun bloc de ``itemResults`` ne porte
      ``votes`` sur un round anonyme.

    Le mode est fige a l'ouverture et annonce aux votants avant qu'ils votent : le
    basculer une fois les votes emis exposerait des gens qui se croyaient anonymes.
    """
    rnd = current_round(room)
    anonymous = bool(rnd and rnd.is_anonymous)
    spec = spec_for(_resolution_strategy(room))
    card_values = _card_values(room)
    # Hisse hors de la boucle : meme requete pour chaque item, sinon un round a
    # N items la relance N fois pour le meme resultat.
    participants = list(room.participants.all())
    items_out = []
    for item in (rnd.items.all() if rnd else []):
        item_responses = responses_of(rnd, item)
        counted = spec.aggregate(item_responses, card_values)
        block = {
            "itemId": item.id,
            "tally": counted["tally"],
            "spread": counted["spread"],
            "anonymous": anonymous,
        }
        if not anonymous:
            by_participant = {r.participant_id: r.payload.get("card") for r in item_responses}
            block["votes"] = [
                {"participantId": str(p.public_id), "cardValue": by_participant[p.id]}
                for p in participants
                if p.id in by_participant
            ]
        items_out.append(block)
    return {
        "itemResults": items_out,
        "anonymous": anonymous,
    }


def participants_list(room):
    rnd = current_round(room)
    complete = _completed_participant_ids(rnd) if rnd else set()
    out = []
    for p in room.participants.all():
        out.append(
            {
                "participantId": str(p.public_id),
                "username": p.display_name,
                "role": p.role,
                "hasVoted": p.id in complete,
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
    my_responses = {}
    result = None
    round_state = RoundState.IDLE
    subject_text = current_item_text(room)
    if rnd:
        round_state = rnd.state
        # Un round peut desormais porter plusieurs items, donc plusieurs
        # Response pour ce participant (tache 3) : `myResponses` les porte
        # toutes, indexees par item.
        my_responses_list = list(Response.objects.filter(round=rnd, participant=participant))
        my_responses = {str(r.item_id): r.payload for r in my_responses_list}
        if rnd.state == RoundState.ACTED:
            acted = rnd.results.first()
            result = acted.chosen_value if acted else None

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
        "myResponses": my_responses,
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
    # aucune cle ``votes`` sur un round anonyme, dans aucun bloc de ``itemResults``
    # — et c'est bien lui qui decide ici.
    #
    # ACTED est inclus car le client traite « revele » et « acte » comme un seul etat
    # d'affichage : l'omettre laissait le meme trou apres la globalisation.
    if round_state in (RoundState.REVEALED, RoundState.ACTED):
        payload["itemResults"] = revealed_payload(room)["itemResults"]
    return payload


_current_round = current_round
# Alias garde pour les tests existants qui appelaient encore le nom prive avant
# que `current_round` ne devienne public (tache 3).
