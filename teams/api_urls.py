from django.urls import path

from .api_views import (
    AcceptInvitationView,
    InvitationDetailView,
    InvitationListCreateView,
    MemberDetailView,
    MemberListView,
    TeamDeckListView,
    TeamDetailView,
    TeamListCreateView,
)

urlpatterns = [
    path("", TeamListCreateView.as_view(), name="team-list"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="invite-accept"),
    path("<int:team_id>/", TeamDetailView.as_view(), name="team-detail"),
    path("<int:team_id>/decks/", TeamDeckListView.as_view(), name="team-deck-list"),
    path("<int:team_id>/members/", MemberListView.as_view(), name="member-list"),
    path("<int:team_id>/members/<int:user_id>/", MemberDetailView.as_view(), name="member-detail"),
    path("<int:team_id>/invitations/", InvitationListCreateView.as_view(), name="invitation-list"),
    path("<int:team_id>/invitations/<int:inv_id>/", InvitationDetailView.as_view(), name="invitation-detail"),
]
