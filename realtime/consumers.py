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
            # Round de correction 2 (brief tache 6a-5) : ajouter un item
            # change `n` (donc le budget 2n et la borne par item de Dot
            # Voting) -- sans rediffuser, l'affichage restait perime jusqu'au
            # prochain jeton pose. Sans effet pour le poker (et tout round
            # pas encore ouvert) : les deux fonctions rendent alors `None`.
            await self._broadcast_live_totals(room)
            await self._broadcast_pending_budgets(room)
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
            # Meme classe de defaut que `item.add` plus haut (round de
            # correction 2) -- retirer un item change `n` exactement comme en
            # ajouter un, et `remove_item` n'invalidait pas l'affichage.
            # `response.totals` reste None ici en pratique (`remove_item`
            # n'est autorise que sur un round encore IDLE, voir sa docstring
            # dans `realtime/services.py`), mais `response.pending` ne depend
            # pas de l'etat du round et se corrige bel et bien -- §8.7 range
            # les deux faits sous le meme intitule ("Totaux en direct et
            # reste a placer").
            await self._broadcast_live_totals(room)
            await self._broadcast_pending_budgets(room)
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
            # Chainage (contrat §8.5, design §7) : un round lie en mode
            # manuel, pas encore resolu, qui vient de devenir courant presente
            # ses candidats au SEUL facilitateur -- jamais une diffusion de
            # groupe suivie d'un masquage cote client. round.select exige deja
            # le facilitateur (_require_facilitator dans select_round), donc
            # self EST sa connexion : self._emit() le lui renvoie directement,
            # sans passer par self.channel_layer, exactement comme state.sync.
            # Le mode auto ne passe jamais ici : select_round l'a deja resolu
            # lui-meme, round.selected porte deja les items resultants.
            rnd = await database_sync_to_async(services.current_round)(room)
            if (
                rnd is not None
                and rnd.source_round_id
                and (rnd.source_rule or {}).get("mode") == "manual"
                and rnd.source_resolved_at is None
            ):
                candidates = await database_sync_to_async(services.chaining_candidates)(room, rnd.id)
                await self._emit("round.candidates", {"roundId": rnd.id, "candidates": candidates})
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
        elif mtype == "round.bind":
            # Declare la liaison de chainage (contrat §8.5, design §7). Ne
            # copie rien -- aucun fait de plus que round.bound n'est necessaire :
            # la liaison ne change ni les items ni l'etat d'aucun round.
            out = await database_sync_to_async(services.bind_round)(
                room, participant, payload.get("roundId"), payload.get("sourceRoundId"), payload.get("rule")
            )
            await self._broadcast("round.bound", out)
        elif mtype == "round.resolve":
            # Valide une selection manuelle (ou rejoue l'idempotence) et copie
            # -- contrat §8.5. items porte deja itemId/originItemId/sourceItemId
            # (realtime/services.py::_chained_items_payload) : pas de retraitement
            # ici, le meme dict part tel quel sur le fait.
            items = await database_sync_to_async(services.resolve_source)(
                room, participant, payload.get("roundId"), item_ids=payload.get("sourceItemIds")
            )
            await self._broadcast("round.resolved", {"roundId": payload.get("roundId"), "items": items})
            await self._broadcast_agenda(room)
            await self._broadcast_current_item(room)
        elif mtype == "vote.open":
            deadline = await database_sync_to_async(services.open_vote)(room, participant)
            deadline_iso = await database_sync_to_async(services.deadline_iso)(room)
            await self._broadcast("vote.opened", {"deadline": deadline_iso})
            await self._broadcast_participation(room)
            # `response.totals` (contrat §8.7, tache 6a corr. 3) : l'ouverture
            # est le SEUL moment ou `live_totals_payload` bascule de `None` a
            # un agregat -- le round passe idle -> open, sa seule garde d'etat.
            # Sans cette diffusion, deux ecrans contradictoires cohabitent dans
            # la meme salle jusqu'au premier jeton pose : ceux deja connectes
            # affichent encore "totaux masques", ceux qui rechargent (via
            # `state.sync`, qui applique la meme garde) voient des zeros. Meme
            # fonction, memes conditions que partout ailleurs -- rien construit
            # quand la config du round ne rend pas les totaux visibles.
            await self._broadcast_live_totals(room)
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
            # Deux diffusions neuves (design dot voting §4-§5, brief tache
            # 6a-5, contrat §8.7), de portee DIFFERENTE -- a ne pas confondre :
            # - `response.totals` : A TOUS, mais seulement si `live_totals_payload`
            #   rend quelque chose -- round `open` ET config du round
            #   `liveTotals: true` (defaut : secret, `None` sinon). Jamais de
            #   lien participant -> jetons : un AGREGAT, rien d'autre.
            # - `response.pending` : au FACILITATEUR SEUL, `audience="facilitator"`
            #   -- filtre A L'EMISSION dans `facilitation_event` ci-dessous,
            #   jamais un masquage cote client. `None` pour le poker (aucune
            #   notion de budget), rien n'est alors diffuse.
            await self._broadcast_live_totals(room)
            await self._broadcast_pending_budgets(room)
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
            # Round de correction 2 (brief tache 6a-5) : reinitialiser vide
            # les reponses -- sans rediffuser, le reste a placer affiche
            # restait perime (l'ancien total, pas le budget plein qui vient
            # de se liberer) jusqu'au prochain jeton pose sur le round
            # suivant. `response.totals` ne repart generalement pas ici (le
            # round est redevenu `idle`) : voir la docstring de
            # `_broadcast_live_totals`.
            await self._broadcast_live_totals(room)
            await self._broadcast_pending_budgets(room)
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

    async def _broadcast_live_totals(self, room):
        """`response.totals` (contrat §8.7) : a TOUT le groupe, mais
        seulement si `live_totals_payload` rend quelque chose (round `open`
        ET config du round `liveTotals: true` -- defaut : secret).

        Appelee non seulement apres `response.cast`, mais aussi apres tout
        evenement qui peut rendre l'affichage perime SANS qu'aucun jeton
        n'ait ete repose (round de correction 2, brief) : `item.add` change
        `n` (donc la borne par item et le budget 2n) et `vote.reset` vide
        les reponses -- sans cette rediffusion, l'ecran gardait des valeurs
        perimees jusqu'au PROCHAIN jeton pose. Apres un `vote.reset` qui
        remet le round a `idle`, cette fonction ne rediffuse rien (le round
        n'est plus `open`) : le client traite deja `vote.wasReset` comme
        l'invalidation de tout affichage du tour precedent, `response.totals`
        y compris."""
        totals = await database_sync_to_async(services.live_totals_payload)(room)
        if totals is not None:
            await self._broadcast("response.totals", totals)

    async def _broadcast_pending_budgets(self, room):
        """`response.pending` (contrat §8.7) : au FACILITATEUR SEUL, filtre
        a l'emission (voir `_broadcast`/`facilitation_event`). Memes
        declencheurs que `_broadcast_live_totals` ci-dessus -- et
        contrairement a elle, `remaining_budgets` ne depend pas de l'etat du
        round : apres un `vote.reset`, elle rend le budget PLEIN (les
        reponses viennent d'etre videes), une valeur fraiche et non perimee,
        diffusee ici aussi."""
        remaining = await database_sync_to_async(services.remaining_budgets)(room)
        if remaining is not None:
            fac_id = await database_sync_to_async(services.facilitator_public_id)(room)
            await self._broadcast(
                "response.pending", {"remaining": remaining}, audience="facilitator", audience_id=fac_id
            )

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

    async def _broadcast(self, mtype, payload, audience=None, audience_id=None):
        """`audience="facilitator"` (brief tache 6a-5, contrat §8.7) : le
        message part quand meme vers TOUT le groupe (`group_send` ne sait pas
        cibler un seul membre) -- il TRANSITE donc jusqu'a la file de CHAQUE
        connexion, sans jamais atteindre leur socket si elles ne sont pas
        visees (voir `facilitation_event` ci-dessous, qui fait le tri). Dire
        qu'il n'en « transite jamais l'octet » serait faux ; dire qu'aucune
        connexion non visee ne l'ECRIT sur SA socket est la formulation
        exacte.

        `audience_id` (round de correction 2, optimisation performance) :
        l'identifiant du destinataire vise, resolu ICI, A L'EMISSION, une
        SEULE fois pour tout le groupe (`services.facilitator_public_id`,
        une requete) -- au lieu de laisser chaque connexion se re-resoudre
        elle-meme a la livraison (`_resolve()` + `is_facilitator()`, deux
        requetes, repetees par connexion). Voir `facilitation_event` pour ce
        que ce choix change sur l'autorite."""
        message = {"type": "facilitation.event", "mtype": mtype, "payload": payload}
        if audience:
            message["audience"] = audience
            message["audienceId"] = audience_id
        await self.channel_layer.group_send(self.group, message)

    async def _error(self, code, message, rejected_type, cid):
        await self._emit("error", {
            "code": code, "message": message, "rejectedType": rejected_type, "cid": cid,
        }, cid)

    async def facilitation_event(self, event):
        # Filtrage a l'emission, PAS a la livraison (round de correction 2) :
        # `event["audienceId"]` est l'identifiant public du facilitateur tel
        # que RESOLU AU MOMENT DE L'EMISSION (`_broadcast`, une seule requete
        # pour tout le groupe) -- compare ici a `self.public_id`, deja connu
        # de CETTE connexion depuis sa jointure (`_handle_join`), sans la
        # moindre requete. Avant ce changement, CHAQUE connexion du groupe
        # se re-resolvait elle-meme (`_resolve()` + `is_facilitator()`, deux
        # requetes) pour repondre a la meme question -- dans une salle
        # pleine, une trentaine de requetes pour UN SEUL jeton pose, sur une
        # machine qui heberge dix applications Django et a deja sature une
        # fois cette annee.
        #
        # CE QUE CELA CHANGE, A DIRE FRANCHEMENT : l'autorite n'est plus
        # RELUE au moment de la livraison, elle est FIGEE au moment de
        # l'emission. La fenetre entre les deux est infime (le temps
        # qu'`await self.channel_layer.group_send(...)` distribue le
        # message aux files des connexions du groupe), et le destinataire
        # ainsi fige est bien celui qui facilitait quand le fait s'est
        # produit -- mais un transfert de main (`facilitator.transfer`,
        # `facilitator.claim`) survenant PILE dans cette fenetre serait
        # honore avec un message de retard : l'ANCIEN facilitateur recevrait
        # ce dernier `response.pending`, pas le nouveau. Acceptable --
        # improbable, sans consequence au-dela d'un affichage en retard d'un
        # seul message -- mais assume et dit ici, pas decouvert plus tard.
        if event.get("audience") == "facilitator":
            audience_id = event.get("audienceId")
            # ECHEC FERME (relecture round de correction 2) : sous l'ancien
            # mecanisme (chaque connexion se re-resolvait elle-meme), une
            # information manquante etait sans consequence -- la connexion
            # allait chercher la verite en base. Avec une comparaison
            # d'identifiants, ce n'est plus vrai : si `audienceId` est
            # ABSENT (`None`), le comparer nu a `self.public_id` peut
            # reussir PAR ACCIDENT pour une connexion dont l'identifiant
            # public ne serait pas encore etabli (`getattr`, pas
            # `self.public_id` : cette connexion n'aurait meme pas
            # l'attribut avant sa jointure) -- `None == None` livrerait
            # alors le fait a TOUT LE MONDE, l'inverse exact de ce que ce
            # filtre existe pour garantir. Le garde-fou `audience_id is
            # None` refuse donc de livrer a QUICONQUE plutot que de risquer
            # cette coincidence. Un fait reserve qu'on laisse tomber par
            # exces de prudence est un defaut visible et corrigeable ; un
            # fait reserve livre a tous serait une fuite silencieuse.
            if audience_id is None or audience_id != getattr(self, "public_id", None):
                return
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
