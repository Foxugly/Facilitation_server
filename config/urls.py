from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("health.urls")),
    path("api/v1/auth/", include("accounts.api_urls")),
    path("api/v1/teams/", include("teams.api_urls")),
    path("api/v1/history/", include("history.api_urls")),
    path("api/v1/board/", include("boards.api_urls")),
    path("api/v1/decks/", include("decks.api_urls")),
    path("api/v1/billing/", include("billing.api_urls")),
    path("api/v1/staff/", include("accounts.api_staff_urls")),
    # Les rooms sont montees a la racine de l'API : elles doivent rester en
    # DERNIER, sinon elles avalent les prefixes ci-dessus.
    path("api/v1/", include("rooms.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
