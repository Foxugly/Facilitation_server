"""Small helpers for consistent API error bodies + OpenAPI error serializers."""
from rest_framework import serializers
from rest_framework.response import Response


def error_response(*, code: str, detail: str, http_status: int) -> Response:
    """A uniform error body: {"code": ..., "detail": ...}."""
    return Response({"code": code, "detail": detail}, status=http_status)


class ErrorResponseSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


def build_validation_error_serializer(name: str, fields: list[str]):
    """Build a DRF serializer describing a 400 validation body (per-field error
    lists), for accurate drf-spectacular schemas."""
    attrs = {
        f: serializers.ListField(child=serializers.CharField(), required=False) for f in fields
    }
    return type(name, (serializers.Serializer,), attrs)
