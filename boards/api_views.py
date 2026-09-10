"""Delegation Board API (design P2.5). Read is member-gated; edits are admin-gated.
The 7 delegation levels come from the standard deck so the board and the cards always
agree. AS-IS / TO-BE are stored as card values ("1".."7") on each row."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.service import paid_required
from config.api_errors import error_response
from decks.models import Deck, TextLayerKind
from teams.models import Team
from teams.permissions import is_manager, is_member

from .models import Board, BoardRow


def _levels():
    """The 7 delegation levels (value + translated name dict) from the standard deck."""
    deck = Deck.objects.filter(is_standard=True).order_by("id").first()
    if deck is None:
        return []
    out = []
    for card in deck.cards.filter(is_active=True).prefetch_related("layers__translations").order_by("order"):
        name = card.value
        for layer in card.layers.all():
            if layer.content_kind == TextLayerKind.I18N:
                from rooms.snapshot import _layer_text

                name = _layer_text(layer)
                break
        out.append({"value": card.value, "name": name})
    return out


def _valid_values():
    return {lvl["value"] for lvl in _levels()}


def _row_dict(row):
    return {
        "id": row.id,
        "topic": row.topic,
        "asIs": row.as_is_level,
        "toBe": row.to_be_level,
        "order": row.order,
    }


def _get_board(team):
    board, _ = Board.objects.get_or_create(team=team)
    return board


class BoardView(APIView):
    """GET the whole board (levels + rows). Auto-creates the board on first access."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_member(team, request.user):
            return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
        board = _get_board(team)
        rows = [_row_dict(r) for r in board.rows.all()]
        return Response({"levels": _levels(), "rows": rows})


class BoardRowListView(APIView):
    """POST a new decision area (admin)."""

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        if (err := paid_required(team)) is not None:
            return err
        topic = (request.data.get("topic") or "").strip()
        if not topic:
            return error_response(code="invalid_topic", detail="Topic is required.", http_status=400)
        board = _get_board(team)
        order = board.rows.count()
        row = BoardRow.objects.create(board=board, topic=topic, order=order)
        return Response(_row_dict(row), status=status.HTTP_201_CREATED)


class BoardRowDetailView(APIView):
    """PATCH (topic / asIs / toBe / order) or DELETE a row (admin). asIs/toBe accept a
    valid card value or null to clear the post-it."""

    permission_classes = [permissions.IsAuthenticated]

    def _row(self, team_id, row_id, user):
        team = get_object_or_404(Team, pk=team_id)
        if not is_manager(team, user):
            return None, error_response(code="forbidden", detail="Manager role required.", http_status=403)
        if (err := paid_required(team)) is not None:
            return None, err
        row = get_object_or_404(BoardRow, pk=row_id, board__team=team)
        return row, None

    def patch(self, request, team_id, row_id):
        row, err = self._row(team_id, row_id, request.user)
        if err:
            return err
        data = request.data
        valid = _valid_values()
        updates = []
        if "topic" in data:
            topic = (data.get("topic") or "").strip()
            if not topic:
                return error_response(code="invalid_topic", detail="Topic is required.", http_status=400)
            row.topic = topic
            updates.append("topic")
        for field, key in (("as_is_level", "asIs"), ("to_be_level", "toBe")):
            if key in data:
                value = data.get(key)
                if value is not None and value not in valid:
                    return error_response(code="invalid_level", detail="Unknown level value.", http_status=400)
                setattr(row, field, value)
                updates.append(field)
        if "order" in data:
            try:
                row.order = int(data.get("order"))
                updates.append("order")
            except (TypeError, ValueError):
                return error_response(code="invalid_order", detail="Order must be an integer.", http_status=400)
        if updates:
            row.save(update_fields=updates)
        return Response(_row_dict(row))

    def delete(self, request, team_id, row_id):
        row, err = self._row(team_id, row_id, request.user)
        if err:
            return err
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
