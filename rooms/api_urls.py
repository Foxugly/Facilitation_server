from django.urls import path

from .api_views import CreateRoomView, JoinRoomView, RoomExistsView


urlpatterns = [
    path("rooms", CreateRoomView.as_view(), name="create-room"),
    path("rooms/<str:code>", RoomExistsView.as_view(), name="room-exists"),
    path("rooms/<str:code>/join", JoinRoomView.as_view(), name="join-room"),
]
