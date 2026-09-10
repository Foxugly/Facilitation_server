from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User


class RegisterSerializer(serializers.ModelSerializer):
    # No validators on email so DRF's auto UniqueValidator doesn't fire — a
    # duplicate must not 400 (enumeration leak); the view handles it anti-leak.
    email = serializers.EmailField(validators=[])
    password = serializers.CharField(write_only=True, min_length=8)
    display_name = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    turnstile_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ["email", "password", "display_name", "turnstile_token"]

    def validate_email(self, value):
        return value.strip().lower()

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data["email"], password=validated_data["password"]
        )
        display_name = (validated_data.get("display_name") or "").strip()
        if display_name:
            user.display_name = display_name
            user.save(update_fields=["display_name"])
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            email=attrs["email"].strip().lower(),
            password=attrs["password"],
        )
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid credentials.")
        attrs["user"] = user
        return attrs


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    turnstile_token = serializers.CharField(write_only=True, required=False, allow_blank=True)


class ResetPasswordConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)


class EmailConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()


class EmailResendSerializer(serializers.Serializer):
    email = serializers.EmailField()


class MagicLinkRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    turnstile_token = serializers.CharField(write_only=True, required=False, allow_blank=True)


class MagicLinkVerifySerializer(serializers.Serializer):
    token = serializers.CharField()


class UserMeSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, user) -> str:
        from config.media import absolute_media_url

        return absolute_media_url(user.avatar)

    class Meta:
        model = User
        # is_staff/is_superuser read-only so the SPA can gate an admin link client-side.
        # subscription_bypass read-only too, but note this serializer is only ever
        # used for reads (MeApiView.get, build_token_response_for_user); the actual
        # PATCH /api/auth/me/ write path uses ProfileUpdateSerializer below, whose
        # narrow fields=["display_name"] is what really blocks self-elevation.
        # read_only_fields here is defense in depth, protecting only if this
        # serializer is ever repurposed for writes.
        fields = [
            "id", "email", "display_name", "avatar_url", "is_active", "email_confirmed",
            "is_staff", "is_superuser", "subscription_bypass",
        ]
        read_only_fields = [
            "id", "email", "is_active", "email_confirmed",
            "is_staff", "is_superuser", "subscription_bypass",
        ]


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["display_name"]


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserMeSerializer()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True, help_text="Refresh token to invalidate")


class StaffUserSerializer(serializers.ModelSerializer):
    """Vue staff d'un compte : identité + état de l'accès offert. Seuls
    subscription_bypass et bypass_note sont mutables ; bypass_granted_at est
    horodaté par la vue, jamais transmis par le client."""

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "subscription_bypass", "bypass_note", "bypass_granted_at"]
        read_only_fields = ["id", "email", "display_name", "bypass_granted_at"]


def build_token_response_for_user(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserMeSerializer(user).data,
    }
