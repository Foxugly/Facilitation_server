"""Televersements d'images du referentiel (dos de cartes, tapis).

Une entree televersee appartient a son auteur (``uploaded_by``) et devient visible
par ses squads (``decks.selection``). Le catalogue d'une equipe est servi par
l'app ``teams``.

Il n'y a plus de catalogue public : une salle sans compte ne choisit rien a la
creation — elle porte tout le catalogue gratuit, change de type de poker en salle,
et son dos est impose. Plus rien a lui presenter, donc.
"""
from django.core.exceptions import ValidationError
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response

from .models import Background, CardBack, Felt
from .selection import can_upload
from .serializers import BackgroundSerializer, CardBackSerializer, FeltSerializer
from .validators import MAX_BACKGROUND_BYTES, MAX_UPLOAD_BYTES, validate_image_upload


class _UploadView(APIView):
    """Shared upload flow for a single-image catalogue entry (card back / felt).

    The entry is owned by the uploader (`uploaded_by`), marked custom
    (`is_standard=False`, `free_tier=False`), and thus visible only to the
    uploader's squads. Only owners/managers may upload.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    model = None
    serializer_class = None
    max_upload_bytes = MAX_UPLOAD_BYTES

    def post(self, request):
        if not can_upload(request.user):
            return error_response(code="forbidden", detail="Only a team owner or manager can upload.", http_status=403)
        image = request.FILES.get("image")
        try:
            validate_image_upload(image, max_bytes=self.max_upload_bytes)
        except ValidationError as e:
            return error_response(code="invalid_image", detail="; ".join(e.messages), http_status=400)
        name = (request.data.get("name") or "").strip()[:120]
        entry = self.model.objects.create(
            is_standard=False, free_tier=False, uploaded_by=request.user, name=name, image=image
        )
        return Response(self.serializer_class(entry).data, status=status.HTTP_201_CREATED)

    def delete(self, request, pk):
        # Only the uploader can delete their own upload. A team referencing it
        # (team.card_back / team.felt) is SET_NULL by the FK.
        entry = self.model.objects.filter(pk=pk, is_standard=False).first()
        if entry is None:
            return error_response(code="not_found", detail="Not found.", http_status=404)
        if entry.uploaded_by_id != request.user.id:
            return error_response(code="forbidden", detail="You can only delete your own upload.", http_status=403)
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CardBackUploadView(_UploadView):
    model = CardBack
    serializer_class = CardBackSerializer


class FeltUploadView(_UploadView):
    model = Felt
    serializer_class = FeltSerializer


class BackgroundUploadView(_UploadView):
    model = Background
    serializer_class = BackgroundSerializer
    max_upload_bytes = MAX_BACKGROUND_BYTES
