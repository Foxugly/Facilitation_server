"""Cloudflare Turnstile server-side verification (fleet pattern).

Rollout is gated on TURNSTILE_SECRET_KEY: when no secret is configured, callers
skip verification (register/forgot keep working), so the captcha can ship and be
activated later by seeding the SSM secret. Once a secret is set, verification is
fail-closed: any missing/invalid token or siteverify failure returns False.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger("facilitation")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0


def turnstile_enabled() -> bool:
    return bool(getattr(settings, "TURNSTILE_SECRET_KEY", ""))


def verify_turnstile_token(token: str, remote_ip: str | None = None) -> bool:
    if not token:
        return False
    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "")
    if not secret:
        logger.error("TURNSTILE_SECRET_KEY not configured; refusing to verify.")
        return False

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        response = requests.post(VERIFY_URL, data=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        logger.exception("Turnstile siteverify network error; treating as failure.")
        return False
    if response.status_code != 200:
        return False
    try:
        return bool(response.json().get("success"))
    except ValueError:
        return False


def get_remote_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
