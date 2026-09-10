from django.urls import path

from .api_views import HistoryDetailView, HistoryEmailView, HistoryListView

urlpatterns = [
    path("<int:team_id>/", HistoryListView.as_view(), name="history-list"),
    path("<int:team_id>/<str:day>/", HistoryDetailView.as_view(), name="history-detail"),
    path("<int:team_id>/<str:day>/email/", HistoryEmailView.as_view(), name="history-email"),
]
