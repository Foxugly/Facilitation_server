"""GDPR data export (right of access / portability) for the authenticated user.

Mirrors trainingmanager_server's ``customuser/export.py``: a plain dict built from
the caller's own rows, so the view stays a thin HTTP layer. Scope rule: only data
about the caller, or data the caller already sees in the app (team names, the
items they answered in the rooms they sat in). Never another user's e-mail,
response or token: the room seat token, invitation tokens, the password hash, the
Stripe ids and the refresh tokens are left out.
"""
from django.utils import timezone


def _iso(value):
    return value.isoformat() if value is not None else None


def build_export(user) -> dict:
    return {
        "exported_at": timezone.now().isoformat(),
        "profile": _profile(user),
        "subscription": _subscription(user),
        "team_memberships": _team_memberships(user),
        "owned_teams": _owned_teams(user),
        "invitations_sent": _invitations_sent(user),
        "invitations_received": _invitations_received(user),
        "rooms": _rooms(user),
        "uploads": _uploads(user),
    }


def _profile(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email_confirmed": user.email_confirmed,
        "avatar": user.avatar.name if user.avatar else None,
        "is_active": user.is_active,
        "date_joined": _iso(user.date_joined),
        "last_login": _iso(user.last_login),
    }


def _subscription(user):
    from billing.models import Subscription

    sub = Subscription.objects.filter(user=user).first()
    if sub is None:
        return None
    return {
        "status": sub.status,
        "plan": sub.plan,
        "interval": sub.interval,
        "current_period_end": _iso(sub.current_period_end),
        "is_paid": sub.is_paid,
        "updated_at": _iso(sub.updated_at),
    }


def _team_memberships(user) -> list:
    return [
        {"team_id": m.team_id, "team": m.team.name, "role": m.role, "joined_at": _iso(m.joined_at)}
        for m in user.team_memberships.select_related("team").order_by("joined_at")
    ]


def _owned_teams(user) -> list:
    return [
        {"id": t.id, "name": t.name, "created_at": _iso(t.created_at)}
        for t in user.owned_teams.order_by("created_at")
    ]


def _invitations_sent(user) -> list:
    from teams.models import Invitation

    return [
        {
            "team": inv.team.name,
            "email": inv.email,
            "role": inv.role,
            "created_at": _iso(inv.created_at),
            "expires_at": _iso(inv.expires_at),
            "accepted_at": _iso(inv.accepted_at),
        }
        for inv in Invitation.objects.filter(invited_by=user).select_related("team").order_by("created_at")
    ]


def _invitations_received(user) -> list:
    from teams.models import Invitation

    return [
        {
            "team": inv.team.name,
            "role": inv.role,
            "created_at": _iso(inv.created_at),
            "expires_at": _iso(inv.expires_at),
            "accepted_at": _iso(inv.accepted_at),
        }
        for inv in Invitation.objects.filter(email__iexact=user.email).select_related("team").order_by("created_at")
    ]


def _rooms(user) -> list:
    """Every room seat held by the caller, with the caller's own contributions only:
    the responses they gave and the items (post-its, agenda points) they wrote.

    A room has no creator column: the seat with the facilitator role is the one
    that opened it (or took it over), hence ``role`` per seat. Items written by
    other participants are left out, so an anonymous brainstorming stays so.
    """
    from rooms.models import Participant

    seats = (
        Participant.objects.filter(user=user)
        .select_related("room", "room__team")
        .prefetch_related("votes__item", "votes__round", "items__round")
        .order_by("created_at")
    )
    out = []
    for seat in seats:
        room = seat.room
        out.append({
            "room_code": room.code,
            "room_title": room.title,
            "team": room.team.name if room.team_id else None,
            "room_created_at": _iso(room.created_at),
            "display_name": seat.display_name,
            "role": seat.role,
            "joined_at": _iso(seat.created_at),
            "last_seen_at": _iso(seat.last_seen_at),
            "responses": [
                {
                    "round": r.round.sequence,
                    "item": r.item.text,
                    "payload": r.payload,
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
                for r in sorted(seat.votes.all(), key=lambda r: r.created_at)
            ],
            "items_authored": [
                {"round": it.round.sequence, "text": it.text, "created_at": _iso(it.created_at)}
                for it in sorted(seat.items.all(), key=lambda it: it.created_at)
            ],
        })
    return out


def _uploads(user) -> list:
    from decks.models import Background, CardBack, Felt

    out = []
    for kind, model in (("card_back", CardBack), ("felt", Felt), ("background", Background)):
        out.extend(
            {"kind": kind, "name": a.name, "file": a.image.name, "created_at": _iso(a.created_at)}
            for a in model.objects.filter(uploaded_by=user).order_by("created_at")
        )
    return out
