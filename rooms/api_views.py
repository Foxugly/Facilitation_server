"""HTTP boundary that creates/resolves a room before the WebSocket opens (contract §1).

HTTP creates/resolves the room and issues the ``participantToken`` + ``deckSnapshot``;
everything *inside* the room happens over the socket (contract §0.5).

Phase 2: a room may be tied to a Team — then it is members-only (auth required),
non-ephemeral, and participants are linked to their user (name from the profile).
"""
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from config.api_errors import error_response
from decks.selection import (
    DELEGATION_POKER_CODE,
    background_for_team,
    card_back_for_team,
    decks_for_team,
    free_decks,
    imposed_free_card_back,
    felt_for_team,
)
from teams.models import Team
from teams.permissions import is_member

from .api_serializers import CreateRoomSerializer, JoinRoomSerializer
from .codes import generate_token, generate_unique_code, normalize_code
from .models import Participant, Role, Room
from .snapshot import apply_team_appearance, build_deck_snapshot


def _live_room_or_none(code):
    room = Room.objects.filter(code=normalize_code(code)).select_related("team").first()
    if room is None or not room.is_live:
        return None
    return room


def _display_name_for(user) -> str:
    return (user.display_name or user.email).strip()


class CreateRoomView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "create_room"

    @transaction.atomic
    def post(self, request):
        serializer = CreateRoomSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        team = None
        user = None
        team_id = data.get("team")
        if team_id is not None:
            if not request.user.is_authenticated:
                return error_response(code="auth_required", detail="Sign in to create a team session.", http_status=401)
            team = get_object_or_404(Team, pk=team_id)
            if not is_member(team, request.user):
                return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
            user = request.user
            display_name = _display_name_for(request.user)
        else:
            display_name = (data.get("username") or "").strip()
            if not display_name:
                return error_response(code="username_required", detail="A display name is required.", http_status=400)

        # Every poker type playable in this room, frozen for its whole life; the
        # facilitator switches between them round by round. A team room takes the
        # team's enabled decks; an account-less room takes the whole free catalogue
        # and an imposed back — elle ne choisit rien a la creation.
        if team is not None:
            decks = decks_for_team(team)
            card_back = card_back_for_team(team)
        else:
            decks = free_decks()
            card_back = imposed_free_card_back()
        if not decks:
            return Response({"detail": "No standard deck configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        snapshots = [build_deck_snapshot(d, card_back) for d in decks]
        if team is not None:
            # Appearance (P2.6): each surface honours the team's chosen style.
            felt = felt_for_team(team)
            background = background_for_team(team)
            for snap in snapshots:
                apply_team_appearance(snap, team, card_back=card_back, felt=felt, background=background)
        snapshot = snapshots[0]
        deck = decks[0]
        code = generate_unique_code(lambda c: Room.objects.filter(code=c).exists())
        room = Room(
            code=code, title=data["title"], vote_type=deck.vote_type, deck_snapshot=snapshot,
            deck_snapshots=snapshots, team=team,
            max_participants=settings.ROOM_MAX_PARTICIPANTS,
            **({"result_layout": team.result_layout} if team is not None else {}),
        )
        room.touch(save=False)
        room.save()

        facilitator = Participant.objects.create(
            room=room, token=generate_token(), display_name=display_name, role=Role.FACILITATOR, user=user
        )
        return Response(
            {
                "code": room.code,
                "roomTitle": room.title,
                "participantToken": facilitator.token,
                "role": Role.FACILITATOR,
                "deckSnapshot": snapshot,
                "availableDecks": snapshots,
                "isTeam": team is not None,
            },
            status=status.HTTP_201_CREATED,
        )


class JoinRoomView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "join_room"

    @transaction.atomic
    def post(self, request, code):
        room = _live_room_or_none(code)
        if room is None:
            return Response({"detail": "Room not found or expired."}, status=status.HTTP_404_NOT_FOUND)

        if room.team_id is not None:
            # Team room: members only, no anonymous guests (scope §4.2).
            if not request.user.is_authenticated:
                return error_response(code="auth_required", detail="Sign in to join this team session.", http_status=401)
            if not is_member(room.team, request.user):
                return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
            # Re-join reuses the member's existing participant (no duplicate seats).
            participant = room.participants.filter(user=request.user).first()
            if participant is None:
                if room.participants.count() >= room.max_participants:
                    return error_response(code="room_full", detail="This room is full.", http_status=403)
                participant = Participant.objects.create(
                    room=room, token=generate_token(), display_name=_display_name_for(request.user),
                    role=Role.VOTER, user=request.user,
                )
        else:
            serializer = JoinRoomSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            username = (serializer.validated_data.get("username") or "").strip()
            if not username:
                return error_response(code="username_required", detail="A display name is required.", http_status=400)
            if room.participants.count() >= room.max_participants:
                return error_response(code="room_full", detail="This room is full.", http_status=403)
            participant = Participant.objects.create(
                room=room, token=generate_token(), display_name=username, role=Role.VOTER
            )

        room.touch()
        return Response(
            {
                "code": room.code,
                "roomTitle": room.title,
                "participantToken": participant.token,
                "role": participant.role,
                "deckSnapshot": room.deck_snapshot,
                "availableDecks": room.deck_snapshots or [room.deck_snapshot],
                "isTeam": room.team_id is not None,
            },
            status=status.HTTP_200_OK,
        )


class RoomExistsView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "join_room"

    def get(self, request, code):
        room = _live_room_or_none(code)
        return Response(
            {
                "code": normalize_code(code),
                "roomTitle": room.title if room else "",
                "exists": room is not None,
                "isTeam": bool(room and room.team_id is not None),
            }
        )
