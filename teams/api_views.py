import re

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response

from billing.service import paid_required, team_is_paid, user_is_paid, user_quota
from decks.selection import available_backgrounds, available_card_backs, available_decks, available_felts
from decks.serializers import BackgroundSerializer, CardBackSerializer, DeckSerializer, FeltSerializer

from .invitations import send_invitation_email
from .models import BackgroundStyle, Invitation, ResultLayout, SurfaceStyle, Team, TeamMembership, TeamRole
from .permissions import is_manager, is_member, is_owner, membership_of
from .serializers import (
    AcceptInviteSerializer,
    InvitationSerializer,
    InviteCreateSerializer,
    MembershipSerializer,
    RoleUpdateSerializer,
    TeamCreateSerializer,
    TeamSerializer,
)

INVITE_TTL_DAYS = 7
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class TeamListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        teams = (
            Team.objects.filter(memberships__user=request.user)
            .select_related("owner")
            .prefetch_related("memberships")
            .distinct()
        )
        return Response(TeamSerializer(teams, many=True, context={"request": request}).data)

    @transaction.atomic
    def post(self, request):
        # Teams are a paid feature (P2.7): require an active subscription and stay within
        # the plan's team quota. Inert until Stripe is configured (creation stays open).
        if not user_is_paid(request.user):
            return error_response(
                code="subscription_required", detail="A subscription is required to create a team.", http_status=402
            )
        if Team.objects.filter(owner=request.user).count() >= user_quota(request.user):
            return error_response(
                code="quota_exceeded", detail="Your plan's team quota is reached.", http_status=402
            )
        serializer = TeamCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = Team.objects.create(name=serializer.validated_data["name"].strip(), owner=request.user)
        TeamMembership.objects.create(team=team, user=request.user, role=TeamRole.OWNER)
        return Response(TeamSerializer(team, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TeamDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _team(self, team_id):
        return get_object_or_404(Team.objects.prefetch_related("memberships"), pk=team_id)

    def get(self, request, team_id):
        team = self._team(team_id)
        if not is_member(team, request.user):
            return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
        return Response(TeamSerializer(team, context={"request": request}).data)

    def patch(self, request, team_id):
        team = self._team(team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        updates = []
        deck_ids_to_set = None
        name = (request.data.get("name") or "").strip()
        if name:
            team.name = name
            updates.append("name")
        # Appearance (P2.6): card-back + felt colours, validated as #RRGGBB[AA].
        # Appearance is a paid feature (P2.7): gated once billing is live.
        if any(f in request.data for f in ("card_back_color", "felt_color", "background_color")):
            if (err := paid_required(team)) is not None:
                return err
        for field in ("card_back_color", "felt_color", "background_color"):
            if field in request.data:
                color = (request.data.get(field) or "").strip()
                if not _HEX_COLOR.match(color):
                    return error_response(code="invalid_color", detail="Expected #RRGGBB.", http_status=400)
                setattr(team, field, color)
                updates.append(field)
        # Fronts (decks) and back are picked independently, and picking is free: what
        # a subscription buys is a *bigger catalogue* (extra decks, custom decks,
        # uploaded backs), which available_decks/available_card_backs enforce — not
        # the gesture of choosing. Only what this team may actually play is accepted,
        # never another team's custom deck or back. Null resets to the default.
        if "deck_ids" in request.data:
            raw = request.data.get("deck_ids") or []
            if not isinstance(raw, list):
                return error_response(code="invalid_deck_ids", detail="Expected a list of deck ids.", http_status=400)
            ids = set(raw)
            if len(available_decks(team).filter(pk__in=ids)) != len(ids):
                return error_response(code="deck_unavailable", detail="One of these decks is not available to this team.", http_status=400)
            deck_ids_to_set = ids
        # Non soumis a paid_required : comme le choix d'un deck ou d'un dos, c'est un
        # reglage d'affichage, pas un element du catalogue qu'un abonnement elargit.
        if "result_layout" in request.data:
            layout = request.data.get("result_layout")
            if layout not in dict(ResultLayout.choices):
                return error_response(code="invalid_result_layout",
                                      detail="Expected 'cards' or 'summary'.", http_status=400)
            team.result_layout = layout
            updates.append("result_layout")
        for style_field in ("card_back_style", "felt_style"):
            if style_field in request.data:
                value = request.data.get(style_field)
                if value not in dict(SurfaceStyle.choices):
                    return error_response(code="invalid_style", detail="Expected 'color' or 'image'.", http_status=400)
                setattr(team, style_field, value)
                updates.append(style_field)
        if "felt_id" in request.data:
            felt_id = request.data.get("felt_id")
            if felt_id is None:
                team.felt = None
            elif not available_felts(team).filter(pk=felt_id).exists():
                return error_response(code="felt_unavailable", detail="This felt is not available to this team.", http_status=400)
            else:
                team.felt_id = felt_id
            updates.append("felt")
        if "card_back_id" in request.data:
            back_id = request.data.get("card_back_id")
            if back_id is None:
                team.card_back = None
            elif not available_card_backs(team).filter(pk=back_id).exists():
                return error_response(code="card_back_unavailable", detail="This card back is not available to this team.", http_status=400)
            else:
                team.card_back_id = back_id
            updates.append("card_back")
        # Le fond a son propre jeu de valeurs : 'theme' en plus, qui n'existe pas
        # pour les autres surfaces et signifie « ne rien imposer ».
        if "background_style" in request.data:
            value = request.data.get("background_style")
            if value not in dict(BackgroundStyle.choices):
                return error_response(code="invalid_style",
                                      detail="Expected 'theme', 'color' or 'image'.", http_status=400)
            team.background_style = value
            updates.append("background_style")
        if "background_id" in request.data:
            background_id = request.data.get("background_id")
            if background_id is None:
                team.background = None
            elif not available_backgrounds(team).filter(pk=background_id).exists():
                return error_response(code="background_unavailable",
                                      detail="This background is not available to this team.", http_status=400)
            else:
                team.background_id = background_id
            updates.append("background")
        if updates:
            team.save(update_fields=updates)
        if deck_ids_to_set is not None:
            team.decks.set(deck_ids_to_set)
        return Response(TeamSerializer(team, context={"request": request}).data)

    def delete(self, request, team_id):
        team = self._team(team_id)
        if not is_owner(team, request.user):
            return error_response(code="forbidden", detail="Only the owner can delete the team.", http_status=403)
        team.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeamDeckListView(APIView):
    """The decks this team may play with, and which one it currently plays.

    Picking from the catalogue is free; ``can_customize`` tells the UI whether to
    offer *extending* it (custom decks, uploaded backs), which a subscription buys.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_member(team, request.user):
            return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
        decks = available_decks(team).prefetch_related("cards", "translations")
        backs = available_card_backs(team)
        return Response(
            {
                "decks": DeckSerializer(decks, many=True).data,
                "selected_deck_ids": list(team.decks.values_list("pk", flat=True)),
                "card_backs": CardBackSerializer(backs, many=True).data,
                "selected_card_back_id": team.card_back_id,
                "felts": FeltSerializer(available_felts(team), many=True).data,
                "selected_felt_id": team.felt_id,
                "backgrounds": BackgroundSerializer(available_backgrounds(team), many=True).data,
                "selected_background_id": team.background_id,
                "can_customize": team_is_paid(team),
            }
        )


class MemberListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_member(team, request.user):
            return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
        members = team.memberships.select_related("user").order_by("joined_at")
        return Response(MembershipSerializer(members, many=True).data)


class MemberDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, team_id, user_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        membership = get_object_or_404(TeamMembership, team=team, user_id=user_id)
        if membership.role == TeamRole.OWNER:
            return error_response(code="cannot_change_owner", detail="The owner's role can't be changed here.", http_status=400)
        serializer = RoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership.role = serializer.validated_data["role"]
        membership.save(update_fields=["role"])
        return Response(MembershipSerializer(membership).data)

    def delete(self, request, team_id, user_id):
        team = get_object_or_404(Team, pk=team_id)
        membership = get_object_or_404(TeamMembership, team=team, user_id=user_id)
        is_self = str(request.user.id) == str(user_id)
        if not (is_self or is_manager(team, request.user)):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        if membership.role == TeamRole.OWNER:
            return error_response(code="cannot_remove_owner", detail="The owner can't leave; transfer ownership first.", http_status=400)
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        pending = team.invitations.filter(accepted_at__isnull=True, expires_at__gt=timezone.now())
        return Response(InvitationSerializer(pending, many=True).data)

    def post(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        serializer = InviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        if team.memberships.filter(user__email__iexact=email).exists():
            return error_response(code="already_member", detail="That person is already a member.", http_status=400)
        if team.memberships.count() >= settings.TEAM_MAX_MEMBERS:
            return error_response(code="team_full", detail="This team has reached its member limit.", http_status=403)
        invitation = Invitation.objects.create(
            team=team,
            email=email,
            role=serializer.validated_data["role"],
            invited_by=request.user,
            expires_at=timezone.now() + timezone.timedelta(days=INVITE_TTL_DAYS),
        )
        send_invitation_email(invitation)
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


class InvitationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, team_id, inv_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        get_object_or_404(Invitation, team=team, pk=inv_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AcceptInvitationView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        serializer = AcceptInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = Invitation.objects.select_related("team").filter(token=serializer.validated_data["token"]).first()
        if invitation is None or not invitation.is_pending:
            return error_response(code="invite_invalid", detail="This invitation is invalid, expired, or already used.", http_status=400)
        # The signed-in user must match the invited email (a leaked token can't be reused by anyone).
        if request.user.email.lower() != invitation.email.lower():
            return error_response(code="invite_email_mismatch",
                                  detail="Sign in with the email this invitation was sent to.", http_status=403)
        already_member = invitation.team.memberships.filter(user=request.user).exists()
        if not already_member and invitation.team.memberships.count() >= settings.TEAM_MAX_MEMBERS:
            return error_response(code="team_full", detail="This team has reached its member limit.", http_status=403)
        TeamMembership.objects.get_or_create(
            team=invitation.team, user=request.user, defaults={"role": invitation.role}
        )
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])
        return Response(TeamSerializer(invitation.team, context={"request": request}).data, status=status.HTTP_200_OK)
