from django.contrib import admin
from django.urls import path
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters

from . import views
from .utils import is_verified, needs_otp


class Admin2FASite(admin.AdminSite):
    """AdminSite whose every view (except login/logout/2FA) requires a verified OTP device."""

    def has_permission(self, request):
        return super().has_permission(request) and is_verified(request.user)

    def login(self, request, extra_context=None):
        # A staff user already past the password step goes straight to the
        # second factor instead of seeing "you are not authorized".
        if request.method == "GET" and needs_otp(request.user):
            return views.redirect_to_otp(request, self)
        response = super().login(request, extra_context)
        # Password just accepted (POST): chain to the second factor.
        if needs_otp(request.user):
            return views.redirect_to_otp(request, self)
        return response

    def _otp_view(self, view):
        def inner(request):
            return view(request, self)

        inner.__name__ = view.__name__
        inner.__doc__ = view.__doc__
        return sensitive_post_parameters("token")(never_cache(csrf_protect(inner)))

    def get_urls(self):
        return [
            path("2fa/setup/", self._otp_view(views.setup), name="2fa_setup"),
            path("2fa/verify/", self._otp_view(views.verify), name="2fa_verify"),
            *super().get_urls(),
        ]
