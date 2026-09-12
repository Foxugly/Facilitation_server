"""Team history — a read model derived from acted results (design P2.4).

Team rooms are non-ephemeral, so every acted ``Result`` persists: history needs no
snapshot table, it is queried live and is therefore always accurate. Acted results
only (no raw votes, scope §3.11). Read is member-gated; the email send is admin-gated.
"""
from datetime import date as date_cls

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.service import paid_required
from config.api_errors import error_response
from realtime.activities import spec_for
from rooms.models import Result, RoundState
from teams.models import Team
from teams.permissions import is_manager, is_member

from .email import send_history_email


def _level_name(deck_snapshot, value):
    """The translated level NAME for a card value (design: show the value, not the number),
    as the card's i18n text dict — the frontend picks the active language. Falls back to
    the raw value for an unknown card/deck."""
    for card in (deck_snapshot or {}).get("cards", []):
        if card.get("value") == value:
            for layer in card.get("layers", []):
                if layer.get("kind") == "i18n":
                    return layer.get("text")
            return value
    return value


def _acted_results(team):
    """Les resultats que l'historique a le droit de montrer : ceux de rounds
    **ACTES**, comme le dit la docstring de ce module (« acted results only »)
    et la carte des apps du CLAUDE.md.

    Ce filtre etait ABSENT, et ne se voyait pas : jusqu'a la tache 6a-4, un
    `Result` n'existait qu'apres l'acte, donc filtrer sur l'existence d'un
    resultat revenait AU MEME -- par accident. Deux choses cassent cet
    accident :

    - une activite qui declare `freeze_results` ecrit son resultat des la
      REVELATION : sans ce filtre, un round revele mais jamais acte
      apparaitrait dans le compte rendu ;
    - un defaut deja present AVANT cette tache : `vote.reset` remet un round a
      IDLE en laissant son `Result` en place (c'est voulu), donc un round
      poker acte PUIS reinitialise -- un round a rejouer, pas une decision --
      restait liste dans l'historique et compte dans les jours. Ce filtre le
      corrige.

    L'agenda (`services.build_agenda`) filtrait deja explicitement sur
    `RoundState.ACTED` pour cette exacte raison ; l'historique diverge.
    """
    return Result.objects.filter(round__room__team=team, round__state=RoundState.ACTED)


def _entries_for(team, day):
    results = (
        _acted_results(team)
        .filter(decided_at__date=day)
        .select_related("item", "round__room")
        .order_by("decided_at")
    )
    out = []
    for r in results:
        room = r.round.room
        snapshot = r.round.deck_snapshot or room.deck_snapshot
        # La FORME de l'entree appartient a l'activite, pas a l'historique :
        # rendre un total de Dot Voting comme le libelle d'une carte
        # produirait un enregistrement faux. `label_for` est passe en
        # fonction pour que le registre n'ait pas a connaitre la forme d'un
        # snapshot de deck.
        spec = spec_for((snapshot or {}).get("resolutionStrategy", ""))
        entry = spec.history_entry(r, lambda value: _level_name(snapshot, value))
        # Les cles de l'historique ecrasent celles de l'activite, jamais
        # l'inverse -- meme regle defensive que le bloc de depouillement.
        out.append(
            {
                **entry,
                "subject": r.item.text,
                "roomCode": room.code,
                "decidedAt": r.decided_at.isoformat(),
            }
        )
    return out


class HistoryListView(APIView):
    """GET the days on which the team acted at least one result, most recent first."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, team_id):
        team = get_object_or_404(Team, pk=team_id)
        if not is_member(team, request.user):
            return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
        rows = (
            _acted_results(team)
            .annotate(day=TruncDate("decided_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("-day")
        )
        days = [{"date": row["day"].isoformat(), "count": row["count"]} for row in rows]
        return Response({"days": days})


class HistoryDetailView(APIView):
    """GET the acted results for one day."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, team_id, day):
        team = get_object_or_404(Team, pk=team_id)
        if not is_member(team, request.user):
            return error_response(code="not_a_member", detail="Not a member of this team.", http_status=403)
        parsed = _parse_day(day)
        if parsed is None:
            return error_response(code="invalid_date", detail="Expected YYYY-MM-DD.", http_status=400)
        return Response({"date": parsed.isoformat(), "entries": _entries_for(team, parsed)})


class HistoryEmailView(APIView):
    """POST to email every team member a login-required link to that day's history."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, team_id, day):
        team = get_object_or_404(Team.objects.prefetch_related("memberships__user"), pk=team_id)
        if not is_manager(team, request.user):
            return error_response(code="forbidden", detail="Manager role required.", http_status=403)
        if (err := paid_required(team)) is not None:
            return err
        parsed = _parse_day(day)
        if parsed is None:
            return error_response(code="invalid_date", detail="Expected YYYY-MM-DD.", http_status=400)
        entries = _entries_for(team, parsed)
        if not entries:
            return error_response(code="empty_day", detail="No results on that day.", http_status=400)
        sent = send_history_email(team, parsed, entries)
        return Response({"sent": sent}, status=status.HTTP_200_OK)


def _parse_day(value):
    try:
        return date_cls.fromisoformat(value)
    except (ValueError, TypeError):
        return None
