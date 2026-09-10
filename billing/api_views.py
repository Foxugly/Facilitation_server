"""Endpoints de facturation, désormais adossés au service central.

Poker ne parle plus à Stripe : il relaie vers billing-api.foxugly.com, signé en
HMAC, et reçoit en retour des droits poussés qu'il met en cache localement.

Le contrat exposé au SPA est **inchangé** : mêmes routes, mêmes formes de réponse.
C'était l'objectif — migrer l'infrastructure sans toucher au frontend d'un site
en production.
"""
import logging

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api_errors import error_response
from teams.models import Team

from . import client
from .models import DeliveryReceipt, Subscription
from .service import billing_configured, user_is_paid, user_quota

logger = logging.getLogger("facilitation")


def _unconfigured():
    return error_response(
        code="billing_unconfigured", detail="Billing is not enabled.", http_status=503
    )


def _unavailable():
    """Le central est branché mais injoignable : 503 explicite, jamais une 500."""
    return error_response(
        code="billing_unavailable",
        detail="Le service de facturation est momentanément indisponible.",
        http_status=503,
    )


def _refused(refus):
    """Relaie le refus du central tel quel.

    Le code et le detail viennent de lui : c'est lui qui sait pourquoi, et le
    SPA doit pouvoir distinguer « vous avez deja un abonnement » d'une panne.
    """
    return error_response(code=refus.code, detail=refus.detail, http_status=refus.status_code)


def _sub_for(user):
    sub, _ = Subscription.objects.get_or_create(user=user)
    return sub


class CheckoutView(APIView):
    """POST {plan, interval} → l'URL d'une session Stripe Checkout, via le central."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not billing_configured():
            return _unconfigured()

        base = request.build_absolute_uri("/")  # non utilisé : le SPA a sa propre URL
        from django.conf import settings

        front = settings.FRONTEND_BASE_URL.rstrip("/")
        try:
            data = client.post(
                "checkout/",
                {
                    "external_user_id": str(request.user.id),
                    "email": request.user.email,
                    "plan": request.data.get("plan"),
                    "interval": request.data.get("interval"),
                    "success_url": f"{front}/teams?billing=success",
                    "cancel_url": f"{front}/teams?billing=cancel",
                },
            )
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()
        return Response({"url": data.get("url", "")})


class PortalView(APIView):
    """POST → l'URL du portail client Stripe, via le central."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not billing_configured():
            return _unconfigured()

        from django.conf import settings

        front = settings.FRONTEND_BASE_URL.rstrip("/")
        try:
            data = client.post(
                "portal/",
                {"external_user_id": str(request.user.id), "return_url": f"{front}/teams"},
            )
        except client.BillingRefused as refus:
            return _refused(refus)
        except client.BillingUnavailable:
            return _unavailable()
        return Response({"url": data.get("url", "")})


class SubscriptionView(APIView):
    """État de facturation du compte (le SPA s'en sert pour afficher plans et quota).

    Servi depuis le cache local : aucun appel réseau, donc la page reste rapide et
    s'affiche même si le central est indisponible.

    Exception : au retour du Checkout (`?refresh=1`), on interroge le central en
    synchrone. Sinon l'utilisateur qui revient de Stripe avant l'arrivée du webhook
    verrait « aucun abonnement » juste après avoir payé (§6.5).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.query_params.get("refresh") and billing_configured():
            self._pull(request.user)

        sub = getattr(request.user, "subscription", None)
        return Response(
            {
                "billingEnabled": billing_configured(),
                "isPaid": user_is_paid(request.user),
                "bypass": bool(getattr(request.user, "subscription_bypass", False)),
                "status": sub.status if sub else "",
                "plan": sub.plan if sub else "",
                "interval": sub.interval if sub else "",
                "quota": user_quota(request.user),
                "teamsUsed": Team.objects.filter(owner=request.user).count(),
                "canManage": bool(sub and sub.stripe_customer_id),
            }
        )

    def _pull(self, user):
        from django.conf import settings

        try:
            payload = client.get(f"entitlements/{settings.BILLING_APP_SLUG}/{user.id}/")
        except (client.BillingUnavailable, client.BillingRefused):
            # Panne comme refus, le geste est le meme : servir le cache plutot
            # que d'echouer. Un rafraichissement de confort ne doit jamais faire
            # echouer l'affichage de la page.
            return
        apply_entitlement(user, payload)


class BillingHistoryView(APIView):
    """Abonnements passés et factures, relayés depuis le central.

    Se dégrade en listes vides plutôt qu'en erreur : la page doit s'afficher même
    si la facturation est coupée ou le central injoignable.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not billing_configured():
            return Response({"billingEnabled": False, "subscriptions": [], "invoices": []})

        try:
            data = client.get(f"history/?external_user_id={request.user.id}")
        except (client.BillingUnavailable, client.BillingRefused):
            return Response({"billingEnabled": True, "subscriptions": [], "invoices": []})

        subscriptions = [
            {
                "id": s.get("id", ""),
                "status": s.get("status", ""),
                "plan": s.get("plan", ""),
                "interval": s.get("interval", ""),
                "startedAt": s.get("started_at"),
                "currentPeriodEnd": s.get("current_period_end"),
                "canceledAt": s.get("canceled_at"),
            }
            for s in data.get("subscriptions", [])
        ]
        invoices = [
            {
                "id": i.get("id", ""),
                "number": i.get("number", ""),
                "status": i.get("status", ""),
                "amountPaid": i.get("amount_paid", 0),
                "currency": i.get("currency", ""),
                "createdAt": i.get("created"),
                "hostedUrl": i.get("hosted_invoice_url", ""),
                "pdfUrl": i.get("invoice_pdf", ""),
            }
            for i in data.get("invoices", [])
        ]
        return Response({"billingEnabled": True, "subscriptions": subscriptions, "invoices": invoices})


def apply_entitlement(user, payload: dict) -> Subscription:
    """Écrit un droit reçu du central dans le cache local."""
    sub = _sub_for(user)
    sub.is_paid = bool(payload.get("is_paid"))
    sub.status = payload.get("status") or ""
    sub.plan = payload.get("plan") or ""
    sub.interval = payload.get("interval") or ""
    sub.quotas = payload.get("quotas") or {}
    period_end = payload.get("current_period_end")
    sub.current_period_end = parse_datetime(period_end) if period_end else None
    grace = payload.get("grace_until")
    sub.grace_until = parse_datetime(grace) if grace else None
    customer_id = payload.get("stripe_customer_id") or ""
    if customer_id:
        sub.stripe_customer_id = customer_id
    sub.save()
    return sub


class EntitlementView(APIView):
    """Reçoit un droit poussé par le central. Remplace l'ancien webhook Stripe.

    Signature HMAC obligatoire. L'idempotence repose sur le `delivery_id` : le
    central rejoue volontiers (reprise, rejeu manuel, réconciliation), et un rejeu
    tardif ne doit pas réappliquer un état périmé.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        if not billing_configured():
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)

        meta = request.META
        if not client.verify_inbound(
            request.method,
            request._request.get_full_path(),
            request.body,
            meta.get("HTTP_X_FOXUGLY_TIMESTAMP", ""),
            meta.get("HTTP_X_FOXUGLY_SIGNATURE", ""),
        ):
            logger.warning("entitlement_bad_signature")
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        payload = request.data

        if payload.get("ping"):
            # Le central teste la connectivite et le secret depuis sa console.
            # Sans ce cas, un cablage PARFAIT retombe sur « delivery_id requis »
            # (400) et le bouton Test annonce un echec -- ce qui est pire qu'une
            # absence de test, puisqu'on cherche alors une panne inexistante.
            # On repond apres la verification de signature : le test doit
            # prouver le secret, pas seulement la joignabilite.
            return Response({"pong": True}, status=status.HTTP_200_OK)

        delivery_id = payload.get("delivery_id")
        if not delivery_id:
            return error_response(code="missing_delivery_id", detail="delivery_id requis.", http_status=400)

        if DeliveryReceipt.objects.filter(pk=delivery_id).exists():
            # 409 : le central compte cette réponse comme une livraison réussie.
            return Response(status=status.HTTP_409_CONFLICT)

        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=payload.get("external_user_id")).first()
        if user is None:
            # Utilisateur supprimé côté Poker : on accuse réception pour que le
            # central cesse de réessayer indéfiniment.
            DeliveryReceipt.objects.create(pk=delivery_id)
            logger.info("entitlement_unknown_user", extra={"id": payload.get("external_user_id")})
            return Response(status=status.HTTP_200_OK)

        apply_entitlement(user, payload)
        DeliveryReceipt.objects.create(pk=delivery_id)
        logger.info("entitlement_applied", extra={"user_id": user.id, "is_paid": payload.get("is_paid")})
        return Response(status=status.HTTP_200_OK)
