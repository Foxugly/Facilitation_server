"""Microsoft Graph transactional email (send-only), mirroring the fleet
(pushit/quizonline/tm). App-only auth via msal; sends from GRAPH_SENDER. Poker
does not receive email, so this is send-only (no inbox polling)."""
import logging

import requests
from django.conf import settings
from msal import ConfidentialClientApplication

logger = logging.getLogger("facilitation")
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_msal_app = None
_msal_tenant = None


def is_configured() -> bool:
    return bool(getattr(settings, "GRAPH_CLIENT_ID", "") and getattr(settings, "GRAPH_SENDER", ""))


def _app():
    global _msal_app, _msal_tenant
    tenant = settings.GRAPH_TENANT_ID
    if _msal_app is None or _msal_tenant != tenant:
        _msal_app = ConfidentialClientApplication(
            settings.GRAPH_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{tenant}",
            client_credential=settings.GRAPH_CLIENT_SECRET,
        )
        _msal_tenant = tenant
    return _msal_app


def _token() -> str:
    result = _app().acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Graph token failed: {result.get('error_description', result)}")
    return result["access_token"]


def send_email(*, to: str, subject: str, body: str) -> None:
    requests.post(
        f"{GRAPH_BASE}/users/{settings.GRAPH_SENDER}/sendMail",
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": False,
        },
        timeout=30,
    ).raise_for_status()
    logger.info("graph_mail_sent", extra={"to": to, "subject": subject})
