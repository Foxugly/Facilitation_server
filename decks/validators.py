"""Server-side validation for user-uploaded catalogue images.

The point isn't the file extension (a client controls that) — it's opening the
bytes with Pillow and confirming they really are an image of an allowed format
and a sane size. This is the part card-assets-spec.md §82-84 flags as the serious
work of user uploads.
"""
import uuid

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 3 * 1024 * 1024  # 3 MB
MAX_BACKGROUND_BYTES = 6 * 1024 * 1024  # 6 MB — a full-bleed backdrop, not a card
MAX_DIMENSION = 3000  # px, either side
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}
# Stored extension per detected format. nginx serves /media/ by extension, so
# the extension must come from the bytes, never from the client's file name.
FORMAT_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


def validate_image_upload(f, max_bytes: int = MAX_UPLOAD_BYTES):
    """Raise ValidationError unless ``f`` is a real, allowed, reasonably-sized image.

    ``max_bytes`` is overridable because a full-bleed background legitimately weighs
    more than a card back; the format and dimension caps stay shared.

    Leaves the file pointer rewound so the caller can save it.
    """
    if f is None:
        raise ValidationError("No image provided.")
    if f.size > max_bytes:
        raise ValidationError(f"Image too large (max {max_bytes // (1024 * 1024)} MB).")

    # verify() checks integrity but leaves the image unusable, so re-open after.
    try:
        f.seek(0)
        Image.open(f).verify()
        f.seek(0)
        img = Image.open(f)
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("Not a valid image file.")

    if img.format not in ALLOWED_FORMATS:
        raise ValidationError(f"Unsupported format {img.format or '?'} (use PNG, JPEG or WEBP).")
    if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
        raise ValidationError(f"Image too big ({img.width}x{img.height}; max {MAX_DIMENSION}px a side).")

    f.seek(0)
    return img.format


def clean_image_upload(f, max_bytes: int = MAX_UPLOAD_BYTES):
    """Validate ``f`` like ``validate_image_upload`` and rename it before storage.

    The client's file name is dropped (audit 2026-10-18, I2): a valid PNG followed
    by HTML and named ``x.html`` used to be stored, and served by nginx, as
    ``text/html`` on the API origin. The stored name is random and its extension
    is the one of the format Pillow detected.
    """
    fmt = validate_image_upload(f, max_bytes=max_bytes)
    f.name = f"{uuid.uuid4().hex}.{FORMAT_EXTENSIONS[fmt]}"
    return f
