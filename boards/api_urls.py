from django.urls import path

from .api_views import BoardRowDetailView, BoardRowListView, BoardView

urlpatterns = [
    path("<int:team_id>/", BoardView.as_view(), name="board"),
    path("<int:team_id>/rows/", BoardRowListView.as_view(), name="board-row-list"),
    path("<int:team_id>/rows/<int:row_id>/", BoardRowDetailView.as_view(), name="board-row-detail"),
]
