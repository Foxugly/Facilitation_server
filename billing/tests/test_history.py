"""Historique de facturation — désormais relayé depuis le service central (lot L4).

Rien n'est miré localement : les tests épinglent les trois comportements qui
comptent — la forme du payload rendu au SPA, la dégradation propre quand le
central est injoignable, et le fait que l'identité interrogée vienne toujours de
l'utilisateur authentifié.
"""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from billing.client import BillingUnavailable

User = get_user_model()

BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "poker",
}

CENTRAL_RESPONSE = {
    "subscriptions": [
        {
            "id": "sub_1",
            "status": "canceled",
            "plan": "team1",
            "interval": "monthly",
            "started_at": "2026-01-01T00:00:00+00:00",
            "current_period_end": "2026-02-01T00:00:00+00:00",
            "canceled_at": "2026-02-01T00:00:00+00:00",
        }
    ],
    "invoices": [
        {
            "id": "in_1",
            "number": "F-001",
            "status": "paid",
            "amount_paid": 500,
            "currency": "EUR",
            "created": "2026-01-01T00:00:00+00:00",
            "hosted_invoice_url": "https://stripe.test/i/1",
            "invoice_pdf": "https://stripe.test/i/1.pdf",
        }
    ],
}


@pytest.fixture
def user(db):
    return User.objects.create_user(email="u@example.com", password="pw12345678", display_name="U")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_anonymous_is_rejected():
    assert APIClient().get("/api/v1/billing/history/").status_code == 401


@pytest.mark.django_db
def test_empty_when_billing_is_unconfigured(client):
    resp = client.get("/api/v1/billing/history/")

    assert resp.status_code == 200
    assert resp.json()["subscriptions"] == []
    assert resp.json()["invoices"] == []


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_returns_subscriptions_and_invoices(client):
    with patch("billing.client.get", return_value=CENTRAL_RESPONSE):
        body = client.get("/api/v1/billing/history/").json()

    sub = body["subscriptions"][0]
    assert (sub["plan"], sub["status"]) == ("team1", "canceled")
    assert sub["canceledAt"] is not None
    inv = body["invoices"][0]
    assert (inv["number"], inv["amountPaid"], inv["currency"]) == ("F-001", 500, "EUR")
    assert inv["pdfUrl"].endswith(".pdf")


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unreachable_central_degrades_instead_of_500(client):
    """La page doit s'afficher même si le central est indisponible."""
    with patch("billing.client.get", side_effect=BillingUnavailable("timeout")):
        resp = client.get("/api/v1/billing/history/")

    assert resp.status_code == 200
    assert resp.json() == {"billingEnabled": True, "subscriptions": [], "invoices": []}


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_queried_identity_comes_from_the_authenticated_user(client, user):
    """Jamais de la requête : sinon n'importe qui lirait l'historique d'un autre."""
    other = User.objects.create_user(email="other@example.com", password="pw12345678")

    with patch("billing.client.get", return_value={"subscriptions": [], "invoices": []}) as called:
        client.get(f"/api/v1/billing/history/?external_user_id={other.id}")

    path = called.call_args.args[0]
    assert f"external_user_id={user.id}" in path
    assert str(other.id) not in path.split("external_user_id=")[1]
