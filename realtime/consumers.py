"""WebSocket consumer for a Delegation Poker room (contract §2-§8).

Server is the source of truth: clients emit intentions, the server validates against
the state machine and *rebroadcasts the fact*. Vote values stay secret until reveal.
Control intentions are accepted only from the facilitator (authority, contract §0.2).
"""
import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from . import services
from .services import RoomError

logger = logging.getLogger("facilitation")

PROTOCOL_VERSION = 1

# Taches de revelation a echeance, par code de room. Au niveau du module et non
# sur l'instance du consumer : la tache doit survivre a la deconnexion du client
# qui a ouvert le vote.
_timer_tasks = {}


class RoomConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.code = self.scope["url_route"]["kwargs"]["code"].upper()
        self.group = f"room_{self.code}"
        self.joined = False
        self.token = None
        await self.accept()

    async def disconnect(self, close_code):
        if self.joined:
            participant = await self._resolve()
            if participant is not None:
                await database_sync_to_async(services.set_connected)(participant, False)
                await self._broadcast("participant.left", {"participantId": self.public_id})
                await self._broadcast_presence(participant.room)
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("v") != PROTOCOL_VERSION:
            return await self._error("protocol.version", "Unsupported protocol version",
                                     content.get("type"), content.get("cid"))
        mtype = content.get("type")
        payload = content.get("payload") or {}
        cid = content.get("cid")

        if mtype == "session.join":
            return await self._handle_join(payload, cid)
        if mtype == "ping":
            return await self._emit("pong", {}, cid)
        if not self.joined:
            return await self._error("token.unknown", "Join the room first", mtype, cid)

        try:
            await self._dispatch(mtype, payload, cid)
        except RoomError as exc:
            await self._error(exc.code, exc.message, exc.rejected_type or mtype, cid)

    # --- intentions -------------------------------------------------------

    async def _dispatch(self, mtype, payload, cid):
        participant = await self._resolve()
        if participant is None:
            return await self._error("token.unknown", "Unknown participant", mtype, cid)
        room = participant.room

        # --- items du round (design 2026-09-11 §5) -----------------------
        if mtype == "item.add":
            item = await database_sync_to_async(services.add_item)(room, participant, payload.get("text", ""))
            await self._broadcast_items(room, "item.added", item["id"])
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "item.update":
            item = await database_sync_to_async(services.update_item)(
                room, participant, payload.get("itemId"), payload.get("text", "")
            )
            await self._broadcast_items(room, "item.updated", item["id"])
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "item.remove":
            item_id = await database_sync_to_async(services.remove_item)(room, participant, payload.get("itemId"))
            await self._broadcast_items(room, "item.removed", item_id)
            await self._broadcast_agenda(room)
        elif mtype == "item.reorder":
            await database_sync_to_async(services.reorder_items)(room, participant, payload.get("itemIds") or [])
            await self._broadcast_items(room, "item.reordered", None)
        elif mtype == "round.add":
            # Ouvre un ROUND DE PLUS dans la file (le scenario) -- a NE PAS
            # confondre avec item.add ci-dessus, qui ajoute un item au round
            # COURANT. Deux semantiques distinctes qui se ressemblent au premier
            # coup d'oeil : round.add avance dans la file, item.add enrichit le
            # tour en cours. Ne diffuse aucun fait propre : agenda.updated et
            # subject.updated (contrat 8.1.a) suffisent a l'agenda a jour.
            await database_sync_to_async(services.add_scenario_item)(room, participant, payload.get("text", ""))
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "round.select":
            out = await database_sync_to_async(services.select_round)(
                room, participant, payload.get("roundId")
            )
            self._cancel_timeout(room.code)
            await self._broadcast("vote.wasReset", {"nextState": "idle"})
            await self._broadcast("round.selected", {**out, "nextState": "idle"})
            await self._broadcast("subject.updated", {"text": out["text"]})
            await self._broadcast_agenda(room)
        elif mtype == "round.prepare":
            summary = await database_sync_to_async(services.prepare_round)(
                room,
                participant,
                subject_id=payload.get("subjectId"),
                subject_text=payload.get("subjectText"),
                anonymous=payload.get("anonymous"),
                deck_id=payload.get("deckId"),
                timer_enabled=payload.get("timerEnabled"),
                timer_seconds=payload.get("timerSeconds"),
            )
            # Announce the composed round to everyone; each is an event the client
            # already applies, so no new client handler is needed.
            self._cancel_timeout(room.code)
            await self._broadcast("vote.wasReset", {"nextState": "idle"})
            await self._broadcast("subject.updated", {"text": summary["subject"]})
            await self._broadcast_agenda(room)
            await self._broadcast("deck.changed", {"deckSnapshot": summary["deckSnapshot"]})
            await self._broadcast("reveal.modeChanged", {"anonymous": summary["anonymous"]})
            await self._broadcast(
                "timer.changed",
                {"enabled": summary["timerEnabled"], "seconds": summary["timerSeconds"]},
            )
            await self._broadcast_participation(room)
        elif mtype == "round.reorder":
            # Refixe Round.sequence sur l'ordre donne (contrat §8.4). Ne
            # diffuse que l'agenda rediffuse : il porte deja l'id du round
            # courant (`status: "current"`), que cette intention ne change
            # jamais -- aucun fait de plus n'est donc necessaire.
            await database_sync_to_async(services.reorder_rounds)(
                room, participant, payload.get("roundIds") or []
            )
            await self._broadcast_agenda(room)
        elif mtype == "round.remove":
            # Elague un round du scenario (contrat §8.4). Refuse par
            # `remove_round` si le round courant est vise (voir son docstring) :
            # l'agenda rediffuse continue donc de designer le meme round
            # courant, d'ou l'absence de fait dedie ici aussi.
            await database_sync_to_async(services.remove_round)(room, participant, payload.get("roundId"))
            await self._broadcast_agenda(room)
        elif mtype == "round.configure":
            out = await database_sync_to_async(services.configure_round)(
                room,
                participant,
                payload.get("roundId"),
                deck_id=payload.get("deckId"),
                config=payload.get("config"),
            )
            await self._broadcast(
                "round.configured",
                {"roundId": out["roundId"], "deckSnapshot": out["deckSnapshot"], "config": out["config"]},
            )
            # deck.changed (contrat §8.3) n'est diffuse que si un deckId a ete
            # fourni : les clients actuels savent deja traiter ce fait, l'omettre
            # laisserait leur affichage de deck perime.
            if payload.get("deckId") is not None:
                await self._broadcast("deck.changed", {"deckSnapshot": out["deckSnapshot"]})
        elif mtype == "vote.open":
            deadline = await database_sync_to_async(services.open_vote)(room, participant)
            deadline_iso = await database_sync_to_async(services.deadline_iso)(room)
            await self._broadcast("vote.opened", {"deadline": deadline_iso})
            await self._broadcast_participation(room)
            self._schedule_timeout(room.code, deadline)
        elif mtype == "response.cast":
            # Ouvert a tous les participants (pas une intention de controle : pas
            # de garde facilitateur). `cast_response` retourne le payload ecrit,
            # jamais rediffuse : la valeur reste secrete jusqu'au reveal
            # (contrat §0.1, invariant d'anonymat).
            await database_sync_to_async(services.cast_response)(
                room, participant, payload.get("itemId"), payload.get("payload") or {}
            )
            await self._broadcast_participation(room)
        elif mtype == "vote.reveal":
            await database_sync_to_async(services.reveal)(room, participant)
            self._cancel_timeout(room.code)
            revealed = await database_sync_to_async(services.revealed_payload)(room)
            await self._broadcast("vote.revealed", {**revealed, "reason": "facilitator"})
        elif mtype == "result.act":
            chosen = await database_sync_to_async(services.act_result)(room, participant, payload.get("chosenValue"))
            await self._broadcast("result.acted", {"chosenValue": chosen})
            await self._broadcast_agenda(room)
        elif mtype == "vote.reset":
            next_state = await database_sync_to_async(services.reset_round)(room, participant)
            self._cancel_timeout(room.code)
            await self._broadcast("vote.wasReset", {"nextState": next_state})
            await self._broadcast_participation(room)
        elif mtype == "reveal.setMode":
            anonymous = await database_sync_to_async(services.set_reveal_mode)(
                room, participant, payload.get("anonymous")
            )
            await self._broadcast("reveal.modeChanged", {"anonymous": anonymous})
        elif mtype == "deck.select":
            snapshot = await database_sync_to_async(services.select_deck)(room, participant, payload.get("deckId"))
            await self._broadcast("deck.changed", {"deckSnapshot": snapshot})
        elif mtype == "timer.set":
            settings_ = await database_sync_to_async(services.set_timer)(
                room, participant, payload.get("enabled"), payload.get("seconds")
            )
            await self._broadcast("timer.changed", settings_)
        elif mtype == "facilitator.transfer":
            new_id = await database_sync_to_async(services.transfer_facilitator)(
                room, participant, payload.get("targetParticipantId")
            )
            await self._broadcast("facilitator.changed", {"newFacilitatorId": new_id})
            await self._broadcast_presence(room)
        elif mtype == "facilitator.claim":
            await self._handle_claim(participant, cid)
        else:
            await self._error("state.invalid_transition", f"Unknown type {mtype}", mtype, cid)

    async def _handle_join(self, payload, cid):
        token = payload.get("participantToken")
        participant = await self._resolve(token)
        if participant is None:
            return await self._error("token.unknown", "Unknown or expired token", "session.join", cid)
        self.token = token
        self.public_id = str(participant.public_id)
        await database_sync_to_async(services.set_connected)(participant, True)
        await self.channel_layer.group_add(self.group, self.channel_name)
        self.joined = True

        state = await database_sync_to_async(services.build_state_sync)(participant)
        await self._emit("state.sync", state, cid)
        await self._broadcast("participant.joined", {
            "participantId": self.public_id,
            "username": participant.display_name,
            "role": participant.role,
        })
        await self._broadcast_presence(participant.room)
        await self._reconcile_timeout(self.code)
        await self._resume_timeout(self.code)

    async def _handle_claim(self, participant, cid):
        allowed = await database_sync_to_async(services.can_claim)(participant.room)
        if not allowed:
            return await self._error("guard.inactive", "Facilitator guard not active", "facilitator.claim", cid)
        await database_sync_to_async(services.promote_facilitator)(participant.room, participant)
        await self._broadcast("facilitator.changed", {"newFacilitatorId": self.public_id})
        await self._broadcast_presence(participant.room)

    # --- helpers ----------------------------------------------------------

    async def _resolve(self, token=None):
        return await database_sync_to_async(services.resolve_participant)(self.code, token or self.token)

    async def _broadcast_participation(self, room):
        data = await database_sync_to_async(services.participation)(room)
        await self._broadcast("participation.update", data)

    async def _broadcast_presence(self, room):
        present = await database_sync_to_async(services.facilitator_present)(room)
        await self._broadcast("facilitator.presence", {"present": present})

    async def _broadcast_agenda(self, room):
        agenda = await database_sync_to_async(services.build_agenda)(room)
        await self._broadcast("agenda.updated", {"agenda": agenda})

    async def _broadcast_items(self, room, mtype, item_id):
        rnd = await database_sync_to_async(services.current_round)(room)
        items = await database_sync_to_async(services.items_payload)(rnd)
        await self._broadcast(mtype, {
            "roundId": rnd.id if rnd else None, "items": items, "itemId": item_id,
        })

    async def _broadcast_current_item(self, room):
        text = await database_sync_to_async(services.current_item_text)(room)
        await self._broadcast("subject.updated", {"text": text})

    async def _emit(self, mtype, payload, cid=None):
        message = {"v": PROTOCOL_VERSION, "type": mtype, "payload": payload}
        if cid:
            message["cid"] = cid
        await self.send_json(message)

    async def _broadcast(self, mtype, payload):
        await self.channel_layer.group_send(
            self.group, {"type": "facilitation.event", "mtype": mtype, "payload": payload}
        )

    async def _error(self, code, message, rejected_type, cid):
        await self._emit("error", {
            "code": code, "message": message, "rejectedType": rejected_type, "cid": cid,
        }, cid)

    async def facilitation_event(self, event):
        await self._emit(event["mtype"], event["payload"])

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
        except Exception:
            logger.exception("timer_reveal_failed", extra={"room_code": code})
        finally:
            if _timer_tasks.get(code) is asyncio.current_task():
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

    async def _resume_timeout(self, code):
        """Reprogramme une tache de revelation a la reconnexion si le round est
        encore OPEN avec une echeance future et qu'aucune tache n'est deja suivie
        pour cette room.

        _reconcile_timeout() (appele juste avant) ne couvre que l'echeance deja
        depassee ; sans ce complement, une echeance encore future perdait toute
        tache a la reconnexion et le round restait bloque en OPEN jusqu'a une
        reconnexion ULTERIEURE a l'echeance -- le cas courant, puisque le SPA se
        reconnecte immediatement apres une coupure.
        """
        room = await database_sync_to_async(services.room_by_code)(code)
        if room is None:
            return
        deadline = await database_sync_to_async(services.open_deadline)(room)
        if deadline is None:
            return
        # Verification et ecriture atomiques (aucun await entre les deux) : sinon
        # plusieurs clients qui se reconnectent en rafale annuleraient et
        # recreeraient la tache plusieurs fois chacun.
        if code not in _timer_tasks:
            self._schedule_timeout(code, deadline)
