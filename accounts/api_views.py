from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from config.api_errors import error_response

from . import refresh_cookie, refresh_grace
from .api_serializers import (
    AccountDeleteSerializer,
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
from .email_confirmation import (
    confirm_email,
    resend_confirmation_email,
    send_confirmation_email,
    send_duplicate_registration_email,
)
from .erasure import erase_account
from .export import build_export
from .magic_link import request_magic_link, verify_magic_link
from .models import User
from .password_reset import confirm_password_reset, request_password_reset
from .refresh_cookie import cookie_origin_guard, deliver_tokens
from .throttles import (
    AccountDeleteRateThrottle,
    AccountExportRateThrottle,
    LoginRateThrottle,
    MagicLinkRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
    ResendEmailRateThrottle,
    UploadRateThrottle,
    with_default_throttles,
)
from .turnstile import get_remote_ip, turnstile_enabled, verify_turnstile_token


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
        if (rejected := cookie_origin_guard(request)) is not None:
            return rejected
        serializer = EmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = confirm_email(serializer.validated_data["uid"], serializer.validated_data["token"])
        if user is None:
            return error_response(code="confirmation_link_invalid",
                                  detail="This confirmation link is invalid or has expired.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        return deliver_tokens(request, Response(build_token_response_for_user(user), status=status.HTTP_200_OK))


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
        if (rejected := cookie_origin_guard(request)) is not None:
            return rejected
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if not user.email_confirmed:
            return error_response(code="email_not_verified",
                                  detail="Please confirm your email address before signing in.",
                                  http_status=status.HTTP_403_FORBIDDEN)
        persist = serializer.validated_data["remember_me"] or not refresh_cookie.wants_cookie(request)
        return deliver_tokens(
            request, Response(build_token_response_for_user(user, persist=persist), status=status.HTTP_200_OK)
        )


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
        if (rejected := cookie_origin_guard(request)) is not None:
            return rejected
        serializer = MagicLinkVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_magic_link(serializer.validated_data["token"])
        if user is None:
            return error_response(code="magic_link_invalid",
                                  detail="This sign-in link is invalid, expired, or already used.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        return deliver_tokens(request, Response(build_token_response_for_user(user), status=status.HTTP_200_OK))


class IsAuthenticatedOrCookieMode(permissions.BasePermission):
    """Logout: a Bearer token, or the cookie-mode opt-in (origin checked in the view).

    Lets the SPA sign out even when its in-memory access token is gone (expired,
    tab reloaded): blacklisting the caller's own refresh cookie needs nothing else.
    """

    def has_permission(self, request, view):
        if refresh_cookie.wants_cookie(request):
            return True
        return bool(request.user and request.user.is_authenticated)


def _blacklist_owned_refresh(request, raw_token):
    """Blacklist ``raw_token`` if the caller may revoke it; return False otherwise.

    An authenticated caller may only revoke ITS OWN refresh token (audit
    2026-10-18, M8): a Bearer for account A must not log account B out. Without
    a Bearer (cookie mode), possession of the token is the only proof there is.
    Raises ``TokenError`` on an invalid or expired token.
    """
    token = RefreshToken(raw_token)
    user = request.user
    if user and user.is_authenticated:
        owner = token.get(jwt_settings.USER_ID_CLAIM)
        if owner is None or str(owner) != str(getattr(user, jwt_settings.USER_ID_FIELD)):
            return False
    token.blacklist()
    return True


class LogoutApiView(APIView):
    """Blacklist the refresh token.

    Body mode (Bearer required): ``{refresh}``, 400 when missing or invalid.
    Cookie mode (``X-Auth-Mode: cookie``, no Bearer needed): blacklists the body
    token or else the HttpOnly cookie, always clears the cookie, 204 (idempotent).
    With a Bearer, only a refresh token of that same user is blacklisted (M8).
    """

    permission_classes = [IsAuthenticatedOrCookieMode]

    def post(self, request):
        if (rejected := cookie_origin_guard(request)) is not None:
            return rejected
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token = serializer.validated_data.get("refresh", "")

        if refresh_cookie.wants_cookie(request):
            # Idempotent: whatever the token's state, the browser ends up signed out.
            refresh_token = refresh_token or refresh_cookie.read_refresh_cookie(request)
            if refresh_token:
                try:
                    _blacklist_owned_refresh(request, refresh_token)
                except TokenError:
                    pass
            response = Response(status=status.HTTP_204_NO_CONTENT)
            refresh_cookie.clear_refresh_cookie(response)
            return response

        if not refresh_token:
            return error_response(code="refresh_token_required", detail="The refresh token is required.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        try:
            owned = _blacklist_owned_refresh(request, refresh_token)
        except Exception:
            owned = False
        if not owned:
            # Same answer for a foreign token as for an invalid one: the
            # endpoint must not confirm that someone else's token is live.
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
    throttle_classes = with_default_throttles(UploadRateThrottle)

    def post(self, request):
        from decks.validators import clean_image_upload

        image = request.FILES.get("image")
        try:
            clean_image_upload(image)
        except DjangoValidationError as e:
            return error_response(code="invalid_image", detail="; ".join(e.messages), http_status=400)
        previous = request.user.avatar.name if request.user.avatar else None
        request.user.avatar = image
        request.user.save(update_fields=["avatar"])
        # Replacing an avatar used to leave the old file behind for good.
        if previous and previous != request.user.avatar.name:
            request.user.avatar.storage.delete(previous)
        return Response(UserMeSerializer(request.user).data, status=status.HTTP_200_OK)

    def delete(self, request):
        if request.user.avatar:
            request.user.avatar.delete(save=False)
            request.user.avatar = None
            request.user.save(update_fields=["avatar"])
        return Response(UserMeSerializer(request.user).data, status=status.HTTP_200_OK)


class AccountDeleteApiView(APIView):
    """POST /api/v1/auth/account/delete/ - delete one's own account (GDPR).

    Same contract as trainingmanager_server: body {current_password}; 204 on
    success; 400 ``current_password_invalid`` (or a field error when missing);
    409 ``owns_teams`` while the caller still owns a team - Team.owner cascades,
    so deleting the account would silently delete the team and its history for
    every member. Ownership must be handed over (or the team deleted) first.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = with_default_throttles(AccountDeleteRateThrottle)

    def post(self, request):
        serializer = AccountDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return error_response(code="current_password_invalid", detail="Current password is incorrect.",
                                  http_status=status.HTTP_400_BAD_REQUEST)
        if user.owned_teams.exists():
            return error_response(code="owns_teams",
                                  detail="Transfer or delete the teams you own before deleting your account.",
                                  http_status=status.HTTP_409_CONFLICT)
        erase_account(user)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        if refresh_cookie.wants_cookie(request):
            refresh_cookie.clear_refresh_cookie(response)
        return response


class AccountExportApiView(APIView):
    """GET /api/v1/auth/account/export/ - GDPR export of one's own data.

    Same idea as trainingmanager_server's ``me/export/``: a JSON document served
    as a download (Content-Disposition: attachment). Contents and exclusions are
    documented in ``accounts/export.py``.
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = with_default_throttles(AccountExportRateThrottle)

    def get(self, request):
        response = Response(build_export(request.user), status=status.HTTP_200_OK)
        filename = f"facilitation-export-{timezone.now():%Y-%m-%d}.json"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Cache-Control"] = "no-store"
        return response


class CustomTokenRefreshView(TokenRefreshView):
    """simplejwt refresh — with ROTATE_REFRESH_TOKENS it also returns a rotated
    refresh token; clients MUST persist it (fleet JWT-rotation memo).

    Cookie mode (``X-Auth-Mode: cookie``): the body ``refresh`` is optional; when
    absent the HttpOnly cookie is used (401 ``refresh_cookie_missing`` if none);
    the rotated token is set in the cookie and omitted from the body. A body
    token sent in cookie mode is the one-time migration path for SPAs that
    still hold one in storage. A token rotated in the last
    ``AUTH_REFRESH_REUSE_GRACE_SECONDS`` is accepted once more from the same
    client (aborted boot refresh, ``accounts/refresh_grace.py``)."""

    def post(self, request, *args, **kwargs):
        if not refresh_cookie.wants_cookie(request):
            return super().post(request, *args, **kwargs)

        if (rejected := cookie_origin_guard(request)) is not None:
            return rejected
        body_token = request.data.get("refresh") if hasattr(request.data, "get") else None
        token = body_token or refresh_cookie.read_refresh_cookie(request)
        if not token:
            return error_response(code="refresh_cookie_missing", detail="No refresh cookie: sign in again.",
                                  http_status=status.HTTP_401_UNAUTHORIZED)
        serializer = TokenRefreshSerializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            # A token rotated seconds ago whose response the browser dropped
            # (reload during the boot refresh): one reissue, see refresh_grace.
            if (pair := refresh_grace.redeem(request, token)) is not None:
                return deliver_tokens(request, Response(pair, status=status.HTTP_200_OK))
            raise InvalidToken(exc.args[0]) from exc
        refresh_grace.remember_rotation(request, token, serializer.validated_data["refresh"])
        return deliver_tokens(request, Response(serializer.validated_data, status=status.HTTP_200_OK))
