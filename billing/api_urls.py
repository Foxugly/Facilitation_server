from django.urls import path

from .api_views import (
    BillingHistoryView,
    CheckoutView,
    EntitlementView,
    PortalView,
    SubscriptionView,
)


urlpatterns = [
    path("subscription/", SubscriptionView.as_view(), name="billing-subscription"),
    path("history/", BillingHistoryView.as_view(), name="billing-history"),
    path("checkout/", CheckoutView.as_view(), name="billing-checkout"),
    path("portal/", PortalView.as_view(), name="billing-portal"),
    # Remplace l'ancien webhook Stripe : c'est desormais le central qui pousse,
    # signe en HMAC. Stripe ne parle plus jamais directement a Poker.
    path("entitlement/", EntitlementView.as_view(), name="billing-entitlement"),
]
