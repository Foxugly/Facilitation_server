from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from config.api_errors import error_response

from .email_confirmation import (
    confirm_email,
    resend_confirmation_email,
    send_confirmation_email,
    send_duplicate_registration_email,
)
from .magic_link import request_magic_link, verify_magic_link
from .models import User
from .password_reset import confirm_password_reset, request_password_reset
from .turnstile import get_remote_ip, turnstile_enabled, verify_turnstile_token
from .api_serializers import (
    EmailConfirmSerializer,
    EmailResendSerializer,
    ForgotPasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MagicLinkRequestSerializer,
    MagicLinkVerifySerializer,
    ProfileUpdateSerializer,
    RegisterSerializer,
    ResetPasswordConfirmSerializer,
    UserMeSerializer,
    build_token_response_for_user,
)
from .throttles import (
    LoginRateThrottle,
    MagicLinkRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
    ResendEmailRateThrottle,
)


def _turnstile_ok(serializer, request) -> bool:
    """Fail-closed captcha check, but only once a secret is configured (§rollout)."""
    if not turnstile_enabled():
        return True
    token = serializer.validated_data.get("turnstile_token") or ""
    return verify_turnstile_token(token, remote_ip=get_remote_ip(request))


class RegisterApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [RegisterRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not _turnstile_ok(serializer, request):
            return error_response(code="captcha_failed", detail="Captcha verification failed.",
                                  http_status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]
        # Anti-enumeration: never reveal whether the address is taken.
        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None:
            send_duplicate_registration_email(existing)
        else:
            send_confirmation_email(serializer.save())
        return Response(
            {"code": "registration_pending_verification",
             "detail": "Account created. Check your inbox to confirm your email before signing in.",
             "email": email},
            status=status.HTTP_201_CREATED,
        )


class ConfirmEmailApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = EmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = confirm_email(serializer.validated_data["uid"], serializer.validated_data["token"])
        if user is None:
            return error_response(code="confirmation_link_invalid",
                                  detail="This confirmation link is invalid or has expired.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        return Response(build_token_response_for_user(user), status=status.HTTP_200_OK)


class ResendConfirmationApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ResendEmailRateThrottle]

    def post(self, request):
        serializer = EmailResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resend_confirmation_email(serializer.validated_data["email"])
        return Response({"code": "ok", "detail": "If that email needs confirmation, a new link has been sent."},
                        status=status.HTTP_200_OK)


class ForgotPasswordApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not _turnstile_ok(serializer, request):
            return error_response(code="captcha_failed", detail="Captcha verification failed.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        request_password_reset(serializer.validated_data["email"])
        return Response({"code": "ok", "detail": "If that email exists, a reset link has been sent."},
                        status=status.HTTP_200_OK)


class ResetPasswordConfirmApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = ResetPasswordConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            ok = confirm_password_reset(data["uid"], data["token"], data["password"])
        except DjangoValidationError as exc:
            return error_response(code="password_invalid", detail=" ".join(exc.messages),
                                  http_status=status.HTTP_400_BAD_REQUEST)
        if not ok:
            return error_response(code="reset_link_invalid",
                                  detail="This reset link is invalid or has expired.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        return Response({"code": "ok", "detail": "Your password has been reset. You can now sign in."},
                        status=status.HTTP_200_OK)


class LoginApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if not user.email_confirmed:
            return error_response(code="email_not_verified",
                                  detail="Please confirm your email address before signing in.",
                                  http_status=status.HTTP_403_FORBIDDEN)
        return Response(build_token_response_for_user(user), status=status.HTTP_200_OK)


class MagicLinkRequestApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [MagicLinkRateThrottle]

    def post(self, request):
        serializer = MagicLinkRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not _turnstile_ok(serializer, request):
            return error_response(code="captcha_failed", detail="Captcha verification failed.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        request_magic_link(serializer.validated_data["email"])
        return Response({"code": "ok", "detail": "If that email is registered, a sign-in link has been sent."},
                        status=status.HTTP_200_OK)


class MagicLinkVerifyApiView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = MagicLinkVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_magic_link(serializer.validated_data["token"])
        if user is None:
            return error_response(code="magic_link_invalid",
                                  detail="This sign-in link is invalid, expired, or already used.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        return Response(build_token_response_for_user(user), status=status.HTTP_200_OK)


class LogoutApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except Exception:
            return error_response(code="refresh_token_invalid", detail="Invalid refresh token.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeApiView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(UserMeSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserMeSerializer(request.user).data, status=status.HTTP_200_OK)


class MeAvatarApiView(APIView):
    """Upload or clear the signed-in user's avatar."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from decks.validators import validate_image_upload

        image = request.FILES.get("image")
        try:
            validate_image_upload(image)
        except DjangoValidationError as e:
            return error_response(code="invalid_image", detail="; ".join(e.messages), http_status=400)
        request.user.avatar = image
        request.user.save(update_fields=["avatar"])
        return Response(UserMeSerializer(request.user).data, status=status.HTTP_200_OK)

    def delete(self, request):
        if request.user.avatar:
            request.user.avatar.delete(save=False)
            request.user.avatar = None
            request.user.save(update_fields=["avatar"])
        return Response(UserMeSerializer(request.user).data, status=status.HTTP_200_OK)


class CustomTokenRefreshView(TokenRefreshView):
    """simplejwt refresh — with ROTATE_REFRESH_TOKENS it also returns a rotated
    refresh token; clients MUST persist it (fleet JWT-rotation memo)."""
