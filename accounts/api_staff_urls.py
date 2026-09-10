from django.urls import path

from .api_staff_views import StaffUserDetailView, StaffUserListView

urlpatterns = [
    path("users/", StaffUserListView.as_view(), name="staff-user-list"),
    path("users/<int:pk>/", StaffUserDetailView.as_view(), name="staff-user-detail"),
]
