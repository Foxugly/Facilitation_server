from rest_framework import serializers

from billing.service import billing_configured, team_is_paid

from .models import Invitation, Team, TeamMembership, TeamRole


class MemberUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    display_name = serializers.CharField()
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, user) -> str:
        from config.media import absolute_media_url

        return absolute_media_url(getattr(user, "avatar", None))


class TeamSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    owner_email = serializers.EmailField(source="owner.email", read_only=True)
    deck_ids = serializers.SerializerMethodField()
    is_paid = serializers.SerializerMethodField()
    billing_enabled = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = [
            "id", "name", "owner_email", "created_at", "my_role", "member_count",
            "card_back_style", "card_back_color", "card_back_id",
            "felt_style", "felt_color", "felt_id",
            "background_style", "background_color", "background_id",
            "result_layout", "deck_ids", "is_paid", "billing_enabled",
        ]

    def get_deck_ids(self, team) -> list:
        return list(team.decks.values_list("pk", flat=True))

    def get_is_paid(self, team) -> bool:
        return team_is_paid(team)

    def get_billing_enabled(self, team) -> bool:
        return billing_configured()

    def get_my_role(self, team) -> str | None:
        user = self.context["request"].user
        m = next((m for m in team.memberships.all() if m.user_id == user.id), None)
        return m.role if m else None

    def get_member_count(self, team) -> int:
        return team.memberships.count()


class TeamCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)


class MembershipSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = TeamMembership
        fields = ["id", "user", "role", "joined_at"]


class RoleUpdateSerializer(serializers.Serializer):
    # Owner role is transferred via a dedicated flow, not this endpoint.
    role = serializers.ChoiceField(choices=[TeamRole.MANAGER, TeamRole.MEMBER])


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = ["id", "email", "role", "created_at", "expires_at", "accepted_at"]


class InviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=[TeamRole.MANAGER, TeamRole.MEMBER], default=TeamRole.MEMBER)

    def validate_email(self, value):
        return value.strip().lower()


class AcceptInviteSerializer(serializers.Serializer):
    token = serializers.CharField()
