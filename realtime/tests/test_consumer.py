"""End-to-end WebSocket cycle over the Channels consumer (contract §4-§6).

Verifies the round state machine, live participation, secret-of-votes (no value
leaks before reveal), and facilitator authority.
"""
import asyncio

import pytest
from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.utils import timezone

from decks.seed import create_standard_deck
from realtime import consumers, services
from realtime.routing import websocket_urlpatterns
from rooms.codes import generate_token, generate_unique_code
from rooms.models import Participant, Role, Room
from rooms.snapshot import build_deck_snapshot


def _age_facilitator(code, seconds):
    """Simulate the facilitator having been absent for `seconds` (guard test)."""
    Room.objects.get(code=code).participants.filter(role=Role.FACILITATOR).update(
        is_connected=False, last_seen_at=timezone.now() - timezone.timedelta(seconds=seconds)
    )


def _role_of(token):
    return Participant.objects.get(token=token).role


def _make_room(with_team=False):
    deck = create_standard_deck()
    # Unique code per test: the process-global InMemoryChannelLayer keys groups by
    # code, so a shared code would leak group state across transactional tests.
    code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
    team = None
    if with_team:
        # The timer is a team-only feature; timer tests need a team room.
        from django.contrib.auth import get_user_model
        from teams.models import Team, TeamMembership, TeamRole

        owner = get_user_model().objects.create_user(
            email=f"owner-{code.lower()}@example.com", password="pw12345678", display_name="O"
        )
        team = Team.objects.create(name=f"T-{code}", owner=owner)
        TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)
    room = Room(code=code, vote_type=deck.vote_type, deck_snapshot=build_deck_snapshot(deck), title="Retro", team=team)
    room.touch(save=False)
    room.save()
    fac = Participant.objects.create(room=room, token=generate_token(), display_name="Sam", role=Role.FACILITATOR)
    voter = Participant.objects.create(room=room, token=generate_token(), display_name="Alex", role=Role.VOTER)
    return code, fac.token, voter.token


async def _join(token, code):
    comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/rooms/{code}/")
    connected, _ = await comm.connect()
    assert connected
    await comm.send_json_to({"v": 1, "type": "session.join", "payload": {"participantToken": token}})
    first = await comm.receive_json_from()
    assert first["type"] == "state.sync"
    return comm, first


async def _drain_until(comm, wanted, pred=None, limit=8):
    for _ in range(limit):
        msg = await comm.receive_json_from()
        if msg["type"] == wanted and (pred is None or pred(msg["payload"])):
            return msg
    raise AssertionError(f"{wanted} not received")


async def _settle(n=10):
    """Give the event loop `n` extra ticks. A cancelled asyncio task only reaches
    its `finally` clause once the loop delivers the CancelledError -- task.cancel()
    merely schedules that; module-level dict assertions right after it are racy
    without a few bare yields first (no real time elapses, this is not a timing
    wait)."""
    for _ in range(n):
        await asyncio.sleep(0)


def _current_subject_id(code):
    return Room.objects.get(code=code).current_session.subject_id


@pytest.mark.django_db(transaction=True)
async def test_full_vote_cycle_and_secret_of_votes():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()

    fac, fac_sync = await _join(fac_token, code)
    assert fac_sync["payload"]["roundState"] == "idle"
    assert len(fac_sync["payload"]["deckSnapshot"]["cards"]) == 7

    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Who owns the budget?"}})
    await _drain_until(voter, "subject.updated")
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")

    # voter casts — participation updates carry IDs/counts, NEVER values (secret §6.a)
    await voter.send_json_to({"v": 1, "type": "vote.cast", "payload": {"cardValue": "5"}})
    part = await _drain_until(fac, "participation.update", pred=lambda p: p["voted"] == 1)
    # Secret of votes (§6.a): participation carries only counts + participant IDs,
    # never a card value — the payload has exactly these three keys.
    assert set(part["payload"].keys()) == {"voted", "total", "votedIds"}
    assert part["payload"]["total"] == 2

    # facilitator reveals → nominative by default: the tally AND who voted what.
    # (Anonymous reveal is a per-round option of paid teams — see test_reveal_mode.)
    await fac.send_json_to({"v": 1, "type": "vote.reveal", "payload": {}})
    revealed = await _drain_until(voter, "vote.revealed")
    assert revealed["payload"]["anonymous"] is False
    assert [v["cardValue"] for v in revealed["payload"]["votes"]] == ["5"]
    assert revealed["payload"]["tally"] == [{"cardValue": "5", "count": 1}]
    assert revealed["payload"]["spread"] == {"min": 5, "max": 5}

    await fac.send_json_to({"v": 1, "type": "result.act", "payload": {"chosenValue": "5"}})
    acted = await _drain_until(voter, "result.acted")
    assert acted["payload"]["chosenValue"] == "5"

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_voter_cannot_open_vote_authority():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "X"}})

    # a voter trying a control intention is rejected, not applied
    await voter.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    err = await _drain_until(voter, "error")
    assert err["payload"]["code"] == "forbidden.not_facilitator"

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_reconnection_restores_vote_and_state():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget?"}})
    await _drain_until(voter, "subject.updated")
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")
    await voter.send_json_to({"v": 1, "type": "vote.cast", "payload": {"cardValue": "3"}})
    await _drain_until(fac, "participation.update", pred=lambda p: p["voted"] == 1)

    # Network drop + reconnect with the same token restores room + vote (contract §8).
    await voter.disconnect()
    voter2, sync2 = await _join(voter_token, code)
    assert sync2["payload"]["roundState"] == "open"
    assert sync2["payload"]["myVote"] == "3"

    await fac.disconnect()
    await voter2.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_facilitator_guard_and_takeover():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    # Facilitator drops → others learn presence=false.
    await fac.disconnect()
    await _drain_until(voter, "facilitator.presence", pred=lambda p: p["present"] is False)

    # A claim within the 60s grace is rejected (a blip must not cost the role).
    await voter.send_json_to({"v": 1, "type": "facilitator.claim", "payload": {}})
    err = await _drain_until(voter, "error")
    assert err["payload"]["code"] == "guard.inactive"

    # After the grace elapses, the first claimer takes over.
    await database_sync_to_async(_age_facilitator)(code, 61)
    await voter.send_json_to({"v": 1, "type": "facilitator.claim", "payload": {}})
    changed = await _drain_until(voter, "facilitator.changed")
    assert changed["payload"]["newFacilitatorId"]
    assert await database_sync_to_async(_role_of)(voter_token) == Role.FACILITATOR

    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_agenda_add_and_select():
    code, fac_token, _ = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)

    await fac.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q1"}})
    await _drain_until(fac, "agenda.updated")
    await fac.send_json_to({"v": 1, "type": "subject.add", "payload": {"text": "Q2"}})
    a2 = await _drain_until(fac, "agenda.updated", pred=lambda p: len(p["agenda"]) == 2)
    agenda = a2["payload"]["agenda"]
    assert [x["text"] for x in agenda] == ["Q1", "Q2"]
    assert agenda[0]["status"] == "current" and agenda[1]["status"] == "pending"

    await fac.send_json_to({"v": 1, "type": "subject.select", "payload": {"subjectId": agenda[1]["id"]}})
    a3 = await _drain_until(fac, "agenda.updated", pred=lambda p: p["agenda"][1]["status"] == "current")
    assert a3["payload"]["agenda"][1]["status"] == "current" and a3["payload"]["agenda"][0]["status"] == "pending"

    await fac.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_facilitator_voluntary_transfer():
    code, fac_token, voter_token = await database_sync_to_async(_make_room)()
    fac, _ = await _join(fac_token, code)
    voter, vsync = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    voter_pid = next(p["participantId"] for p in vsync["payload"]["participants"] if p["role"] == "voter")
    await fac.send_json_to({"v": 1, "type": "facilitator.transfer", "payload": {"targetParticipantId": voter_pid}})
    changed = await _drain_until(voter, "facilitator.changed")
    assert changed["payload"]["newFacilitatorId"] == voter_pid
    assert await database_sync_to_async(_role_of)(voter_token) == Role.FACILITATOR
    assert await database_sync_to_async(_role_of)(fac_token) == Role.VOTER

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_timeout_reveals_on_reconnect_reconciliation():
    """The scheduled asyncio task is best-effort; the deadline in DB is authoritative.
    Simulate a lost/expired task (e.g. service restart) by backdating vote_deadline
    directly, then verify a reconnect triggers _reconcile_timeout and reveals."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)(True)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "timer.set", "payload": {"enabled": True, "seconds": 30}})
    await _drain_until(voter, "timer.changed")
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget?"}})
    await _drain_until(voter, "subject.updated")
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    opened = await _drain_until(voter, "vote.opened")
    assert opened["payload"]["deadline"] is not None

    def _expire():
        room = Room.objects.get(code=code)
        session = room.current_session
        session.vote_deadline = timezone.now() - timezone.timedelta(seconds=1)
        session.save(update_fields=["vote_deadline"])

    await database_sync_to_async(_expire)()

    # Disconnect + reconnect: the module-level timer task (if any) is orphaned on
    # the old consumer instance's room key, but reconciliation on join catches up.
    await voter.disconnect()
    voter2, sync2 = await _join(voter_token, code)
    revealed = await _drain_until(voter2, "vote.revealed")
    assert revealed["payload"]["reason"] == "timeout"
    assert sync2["payload"]["roundState"] == "open"  # state.sync was built before reconciliation ran

    await fac.disconnect()
    await voter2.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_scheduled_timeout_reveals_without_reconnect(monkeypatch):
    """The asyncio task scheduled by vote.open (_schedule_timeout/_fire_timeout) must
    itself fire the reveal -- this is the whole point of the timer feature, and it is
    distinct from test_timeout_reveals_on_reconnect_reconciliation above, which only
    exercises the lazy reconciliation on reconnect (no scheduled task involved there:
    the deadline is backdated directly in DB and a fresh join triggers the catch-up).
    Deterministic and fast: TIMER_MIN_SECONDS is patched to 0 so the deadline is
    effectively immediate, no reliance on real-time sleep."""
    monkeypatch.setattr(services, "TIMER_MIN_SECONDS", 0)
    code, fac_token, voter_token = await database_sync_to_async(_make_room)(True)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "timer.set", "payload": {"enabled": True, "seconds": 0}})
    await _drain_until(voter, "timer.changed")
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget?"}})
    await _drain_until(voter, "subject.updated")
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")

    # No disconnect/reconnect anywhere in this test: if this reveals, it can only be
    # the scheduled asyncio task (_fire_timeout -> _reconcile_timeout), not the
    # reconnect-time reconciliation path covered by the other test.
    revealed = await _drain_until(voter, "vote.revealed", limit=10)
    assert revealed["payload"]["reason"] == "timeout"

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_timer_task_dict_survives_cancellation_races(monkeypatch):
    """Non-regression for the `_timer_tasks` module dict bookkeeping in consumers.py.

    Kills two independent mutants a review flagged as passing the suite unnoticed:

    (1) restoring the unconditional `finally: _timer_tasks.pop(code, None)` in
        _fire_timeout (the original bug) -- a *cancelled* task's cleanup evicts
        whatever task now occupies its room-code slot, even if that's a brand new
        one scheduled after it;
    (2) dropping the `self._cancel_timeout(room.code)` call from the subject.select
        branch of _dispatch -- a pending task then survives untouched instead of
        being cancelled the moment the facilitator switches subjects.

    TIMER_MIN_SECONDS is patched to 0 (as test_scheduled_timeout_reveals_without_reconnect
    does) only to allow a small, comfortably-nonzero `seconds` below the normal 10s
    floor. The delay is never waited out for real: every cancellation below is
    explicit (vote.open / subject.select / vote.reset), asyncio.sleep(delay) is
    aborted instantly by .cancel(), and _settle() gives the loop a few bare ticks
    (no real time) to actually deliver the CancelledError into each task's `finally`.
    """
    monkeypatch.setattr(services, "TIMER_MIN_SECONDS", 0)
    code, fac_token, voter_token = await database_sync_to_async(_make_room)(True)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "timer.set", "payload": {"enabled": True, "seconds": 5}})
    await _drain_until(voter, "timer.changed")
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget?"}})
    await _drain_until(voter, "subject.updated")

    # 1) Open the vote: task A is registered for the room under its code.
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "participation.update")
    await _settle()
    task_a = consumers._timer_tasks.get(code)
    assert task_a is not None and not task_a.done()

    # 2) subject.set while OPEN creates a brand new IDLE session (services.set_subject
    # always takes the "create a new subject+session" branch when the current one
    # isn't idle) -- but the subject.set branch of _dispatch never touches
    # _timer_tasks. Task A is left exactly as it was: still tracked, still alive.
    # (Harmless if it ever fired: reveal_on_timeout() guards on round state, and the
    # round is idle again.)
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "New topic?"}})
    await _drain_until(voter, "subject.updated")
    await _settle()
    assert consumers._timer_tasks.get(code) is task_a
    assert not task_a.done()

    # 3) Open the (new) vote again: _schedule_timeout cancels A synchronously and
    # installs task B under the same room-code key. The critical assertion: once the
    # loop has actually delivered A's CancelledError (and run its finally clause), B
    # must still be the tracked task -- the original bug's unconditional pop() would
    # have evicted B here, because A's finally ran *after* B already replaced it.
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "participation.update")
    await _settle()
    task_b = consumers._timer_tasks.get(code)
    assert task_b is not None and task_b is not task_a

    await _settle()
    # Note: _fire_timeout catches asyncio.CancelledError and swallows it (`pass`)
    # rather than re-raising, so the task ends up merely *done*, not `.cancelled()`
    # -- that's existing behaviour of the finally-based cleanup, not a mutant.
    assert task_a.done()
    assert consumers._timer_tasks.get(code) is task_b, (
        "task B was evicted by task A's cancellation cleanup -- the "
        "'finally: _timer_tasks.pop(code, None)' regression"
    )
    assert not task_b.done()

    # 4) subject.select on the very subject/session currently open must cancel B
    # immediately (the _cancel_timeout() call in the subject.select branch). Without
    # it, B would survive untouched here.
    subject_id = await database_sync_to_async(_current_subject_id)(code)
    await fac.send_json_to({"v": 1, "type": "subject.select", "payload": {"subjectId": subject_id}})
    await _drain_until(voter, "agenda.updated")
    assert code not in consumers._timer_tasks, (
        "task B was not cancelled by subject.select -- the "
        "'self._cancel_timeout(room.code)' call is missing from that branch"
    )
    await _settle()
    assert task_b.done()

    # 5) Open once more (the reselected subject is idle again, same non-empty text):
    # task C is registered.
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "participation.update")
    await _settle()
    task_c = consumers._timer_tasks.get(code)
    assert task_c is not None and not task_c.done()

    # 6) vote.reset must cancel C explicitly: the dict ends up empty and nothing is
    # left running for this room code.
    await fac.send_json_to({"v": 1, "type": "vote.reset", "payload": {}})
    await _drain_until(voter, "participation.update")
    assert code not in consumers._timer_tasks
    await _settle()
    assert task_c.done()

    # Belt and braces: nothing from any phase of this test is still alive.
    assert task_a.done() and task_b.done() and task_c.done()

    await fac.disconnect()
    await voter.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_timer_resumes_on_reconnect_after_restart(monkeypatch):
    """The gap this closes: _reconcile_timeout alone only reveals an *already
    passed* deadline on reconnect -- a still-open round with a *future* deadline
    was never rescheduled. A service restart destroys `_timer_tasks` (module-level,
    in-process) but not the persisted `vote_deadline`; if clients reconnect
    *before* that deadline (the realistic case -- the SPA reconnects immediately),
    nothing was left to ever fire the reveal. The round stayed OPEN forever until
    some later reconnect happened to land after the deadline.

    Simulate the restart precisely: cancel the scheduled task the way process
    death would (not just forget it in the dict -- an orphaned-but-still-running
    task would race the newly-resumed one and mask a missing reschedule), then
    clear `_timer_tasks`. The deadline in DB is left untouched by that, exactly
    as a real restart would leave it. To keep the test fast without waiting out
    the real timer.seconds, the deadline is then pulled to a near-future instant
    via a direct DB write (the same trick `_expire()` above uses for the past
    case, just with a positive delta) -- only the final "does it actually fire"
    step below waits any real (sub-second) time."""
    monkeypatch.setattr(services, "TIMER_MIN_SECONDS", 0)
    code, fac_token, voter_token = await database_sync_to_async(_make_room)(True)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "timer.set", "payload": {"enabled": True, "seconds": 30}})
    await _drain_until(voter, "timer.changed")
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget?"}})
    await _drain_until(voter, "subject.updated")
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")
    await _settle()
    task_a = consumers._timer_tasks.get(code)
    assert task_a is not None and not task_a.done()

    # Simulate the service restart: kill the scheduled task the way process death
    # would, and wipe the module-level bookkeeping dict.
    task_a.cancel()
    consumers._timer_tasks.clear()
    await _settle()
    assert task_a.done()

    def _pull_deadline_near():
        room = Room.objects.get(code=code)
        session = room.current_session
        session.vote_deadline = timezone.now() + timezone.timedelta(milliseconds=400)
        session.save(update_fields=["vote_deadline"])

    await database_sync_to_async(_pull_deadline_near)()

    # Reconnect: _reconcile_timeout must NOT reveal yet (deadline is still
    # future) but the new resume-on-reconnect logic must pick tracking back up.
    await voter.disconnect()
    voter2, sync2 = await _join(voter_token, code)
    # _handle_join's tail (reconcile + resume) keeps running on the background
    # application task after _join() returns with only the first message
    # (state.sync); both involve real database_sync_to_async thread-pool calls,
    # so a real sleep -- not a bare _settle() tick -- is needed to let it land
    # before we inspect module state.
    await asyncio.sleep(0.1)
    assert sync2["payload"]["roundState"] == "open"  # confirms it wasn't already revealed
    task_b = consumers._timer_tasks.get(code)
    assert task_b is not None and not task_b.done(), (
        "reconnect with a still-open round and a future deadline must resume a "
        "timer task -- _reconcile_timeout alone never reschedules"
    )

    revealed = await _drain_until(voter2, "vote.revealed", limit=10)
    assert revealed["payload"]["reason"] == "timeout"
    # Receiving the broadcast only proves _fire_timeout reached the `await
    # self._broadcast(...)` line, not that it has finished (the `finally` pop is
    # still one scheduling step away). Await the task itself so it is
    # deterministically done before the test's event loop is torn down --
    # otherwise this is a rare source of a "Task was destroyed but it is
    # pending" warning at suite teardown, timing-dependent under load.
    await asyncio.wait_for(task_b, timeout=1)
    assert task_b.done()
    assert code not in consumers._timer_tasks

    await fac.disconnect()
    await voter2.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_reconnect_does_not_duplicate_tracked_timer_task():
    """A reconnect must not touch a timer task that's already tracked: it should
    neither cancel it nor schedule a second one. Without the `code not in
    _timer_tasks` guard in `_resume_timeout`, four clients reconnecting in a
    burst would each cancel-and-recreate the task, discarding the delay computed
    at the original schedule and risking a cancel racing a legitimate fire."""
    code, fac_token, voter_token = await database_sync_to_async(_make_room)(True)
    fac, _ = await _join(fac_token, code)
    voter, _ = await _join(voter_token, code)
    await _drain_until(fac, "participant.joined")

    await fac.send_json_to({"v": 1, "type": "timer.set", "payload": {"enabled": True, "seconds": 30}})
    await _drain_until(voter, "timer.changed")
    await fac.send_json_to({"v": 1, "type": "subject.set", "payload": {"text": "Budget?"}})
    await _drain_until(voter, "subject.updated")
    await fac.send_json_to({"v": 1, "type": "vote.open", "payload": {}})
    await _drain_until(voter, "vote.opened")
    await _settle()
    task_a = consumers._timer_tasks.get(code)
    assert task_a is not None and not task_a.done()

    await voter.disconnect()
    voter2, _ = await _join(voter_token, code)
    # Same reasoning as above: real sleep, not _settle(), to let the join tail's
    # thread-pool DB calls actually complete before inspecting _timer_tasks.
    await asyncio.sleep(0.1)

    assert consumers._timer_tasks.get(code) is task_a, (
        "reconnect must not replace an already-tracked timer task"
    )
    assert not task_a.done()

    task_a.cancel()
    await _settle()
    await fac.disconnect()
    await voter2.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_unknown_token_rejected():
    code, _, _ = await database_sync_to_async(_make_room)()
    comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/rooms/{code}/")
    connected, _ = await comm.connect()
    assert connected
    await comm.send_json_to({"v": 1, "type": "session.join", "payload": {"participantToken": "bogus"}})
    msg = await comm.receive_json_from()
    assert msg["type"] == "error" and msg["payload"]["code"] == "token.unknown"
    await comm.disconnect()
