"""Absolute media URLs for API responses.

The frontend is served from a different host than the media (facilitation.foxugly.com
vs facilitation-api.foxugly.com), so a bare ``/media/...`` path would 404 there. Every
serializer that returns an image URL must make it absolute — the room snapshot
already does this; this is the shared helper for the rest.
"""
from django.conf import settings


def absolute_media_url(image_field) -> str:
    if not image_field:
        return ""
    url = image_field.url
    if url.startswith("http"):
        return url
    return f"{settings.PUBLIC_MEDIA_BASE_URL.rstrip('/')}{url}"
