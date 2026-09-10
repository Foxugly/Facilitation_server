from django.urls import path

from .api_views import BackgroundUploadView, CardBackUploadView, FeltUploadView


urlpatterns = [
    path("card-backs/", CardBackUploadView.as_view(), name="card-back-upload"),
    path("card-backs/<int:pk>/", CardBackUploadView.as_view(), name="card-back-detail"),
    path("felts/", FeltUploadView.as_view(), name="felt-upload"),
    path("felts/<int:pk>/", FeltUploadView.as_view(), name="felt-detail"),
    path("backgrounds/", BackgroundUploadView.as_view(), name="background-upload"),
    path("backgrounds/<int:pk>/", BackgroundUploadView.as_view(), name="background-detail"),
]
