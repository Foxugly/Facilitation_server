"""Team role helpers used by the views to gate actions."""
from .models import TeamMembership, TeamRole


def membership_of(team, user):
    return TeamMembership.objects.filter(team=team, user=user).first()


def is_member(team, user) -> bool:
    return membership_of(team, user) is not None


def is_manager(team, user) -> bool:
    """Owner or manager — administers the team: members, invitations, settings,
    board and history. Unrelated to who facilitates a round (see rooms.Role)."""
    m = membership_of(team, user)
    return m is not None and m.role in (TeamRole.OWNER, TeamRole.MANAGER)


def is_owner(team, user) -> bool:
    m = membership_of(team, user)
    return m is not None and m.role == TeamRole.OWNER
