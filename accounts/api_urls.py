from django.urls import path

from .api_views import (
    ConfirmEmailApiView,
    CustomTokenRefreshView,
    ForgotPasswordApiView,
    LoginApiView,
    LogoutApiView,
    MagicLinkRequestApiView,
    MagicLinkVerifyApiView,
    MeApiView,
    MeAvatarApiView,
    RegisterApiView,
    ResendConfirmationApiView,
    ResetPasswordConfirmApiView,
)

urlpatterns = [
    path("register/", RegisterApiView.as_view(), name="register"),
    path("login/", LoginApiView.as_view(), name="login"),
    path("logout/", LogoutApiView.as_view(), name="logout"),
    path("token/refresh/", CustomTokenRefreshView.as_view(), name="token-refresh"),
    path("me/", MeApiView.as_view(), name="me"),
    path("me/avatar/", MeAvatarApiView.as_view(), name="me-avatar"),
    path("email/confirm/", ConfirmEmailApiView.as_view(), name="email-confirm"),
    path("email/resend/", ResendConfirmationApiView.as_view(), name="email-resend"),
    path("forgot-password/", ForgotPasswordApiView.as_view(), name="forgot-password"),
    path("reset-password/", ResetPasswordConfirmApiView.as_view(), name="reset-password"),
    path("magic-link/", MagicLinkRequestApiView.as_view(), name="magic-link"),
    path("magic-link/verify/", MagicLinkVerifyApiView.as_view(), name="magic-link-verify"),
]
