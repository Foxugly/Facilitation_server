"""Personal data in logs (audit 2026-10-18, M2; OPERATIONS.md §3.25).

INFO records become Sentry breadcrumbs and ``logger.exception`` creates an
event: whatever ``extra`` carries reaches Sentry regardless of
``send_default_pii``. E-mail addresses are therefore masked before logging.
"""


def mask_email(address) -> str:
    """``john.doe@example.com`` -> ``j***@example.com`` (domain kept for triage)."""
    if not isinstance(address, str) or "@" not in address:
        return "***"
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"
