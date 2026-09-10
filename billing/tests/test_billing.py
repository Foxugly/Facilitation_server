"""Gating de facturation, désormais adossé au service central (lot L4).

La facturation est gatée sur la configuration du client : non configurée (tests,
et prod jusqu'à ce que les variables soient seedées), les équipes restent
ouvertes, le quota est illimité et le checkout répond 503.

Ce qui a changé depuis la version Stripe-directe : « configuré » ne teste plus une
clé Stripe mais l'accès au central, et `is_paid` n'est plus déduit d'un statut
Stripe local — il est **poussé** par le central, qui a déjà appliqué la période
de grâce.
"""
import json
import time
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from billing.client import _sign
from billing.models import DeliveryReceipt, Subscription
from billing.service import (
    billing_configured,
    quota_for_plan,
    team_is_paid,
    user_is_paid,
    user_quota,
)
from teams.models import Team

User = get_user_model()

BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "poker",
}


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="o@example.com", password="pw12345678", display_name="Owner")


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _paid_sub(user, **kwargs):
    """Le cache local tel que le central l'aurait rempli."""
    defaults = {"is_paid": True, "status": "active", "plan": "team1", "quotas": {"teams": 1}}
    defaults.update(kwargs)
    return Subscription.objects.create(user=user, **defaults)


# ------------------------------------------------------------------ mode inerte

@pytest.mark.django_db
def test_unconfigured_billing_is_inert(owner):
    """C'est ce qui permet de déployer la migration sans rien changer en prod."""
    assert billing_configured() is False
    assert user_is_paid(owner) is True
    assert user_quota(owner) >= 1
    assert _client(owner).post("/api/v1/teams/", {"name": "A"}, format="json").status_code == 201
    body = _client(owner).get("/api/v1/billing/subscription/").json()
    assert body["billingEnabled"] is False and body["isPaid"] is True


@pytest.mark.django_db
def test_checkout_portal_503_when_unconfigured(owner):
    c = _client(owner)
    assert c.post("/api/v1/billing/checkout/", {"plan": "team1", "interval": "monthly"}, format="json").status_code == 503
    assert c.post("/api/v1/billing/portal/", {}, format="json").status_code == 503


@pytest.mark.django_db
def test_entitlement_push_503_when_unconfigured(db):
    assert APIClient().post("/api/v1/billing/entitlement/", {}, format="json").status_code == 503


# ------------------------------------------------------------------------ gating

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_configured_gates_team_creation(owner):
    assert billing_configured() is True
    assert user_is_paid(owner) is False
    assert user_quota(owner) == 0
    r = _client(owner).post("/api/v1/teams/", {"name": "A"}, format="json")
    assert r.status_code == 402 and r.json()["code"] == "subscription_required"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_quota_enforced_for_a_paid_subscription(owner):
    _paid_sub(owner)

    assert user_quota(owner) == 1
    c = _client(owner)
    assert c.post("/api/v1/teams/", {"name": "A"}, format="json").status_code == 201
    r = c.post("/api/v1/teams/", {"name": "B"}, format="json")
    assert r.status_code == 402 and r.json()["code"] == "quota_exceeded"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_quotas_pushed_by_the_central_win_over_the_local_fallback(owner):
    """Le central fait autorité : changer un quota ne doit pas exiger un déploiement."""
    _paid_sub(owner, plan="team1", quotas={"teams": 3})

    assert user_quota(owner) == 3


@override_settings(**BILLING_ON, PLAN_QUOTAS={"team1": 1, "team5": 5})
@pytest.mark.django_db
def test_the_local_fallback_applies_when_the_central_pushed_no_quota(owner):
    _paid_sub(owner, plan="team5", quotas={})

    assert user_quota(owner) == 5
    assert quota_for_plan("team5") == 5


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unpaid_subscription_grants_nothing(owner):
    """is_paid vient du central : Poker ne réinterprète aucun statut Stripe."""
    Subscription.objects.create(user=owner, is_paid=False, status="past_due", plan="team1")

    assert user_is_paid(owner) is False
    assert user_quota(owner) == 0


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_expired_cache_closes_access_even_if_the_central_went_silent(owner):
    """Filet : si le central se tait longtemps, le cache expire au lieu d'ouvrir
    l'accès indéfiniment."""
    _paid_sub(owner, current_period_end=timezone.now() - timezone.timedelta(days=1))

    assert user_is_paid(owner) is False


# ------------------------------------------------------------------------ bypass

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_bypass_grants_paid_and_unlimited_quota(owner):
    assert user_is_paid(owner) is False and user_quota(owner) == 0
    owner.subscription_bypass = True
    owner.save()

    assert user_is_paid(owner) is True
    assert user_quota(owner) == 10_000


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_bypass_owner_makes_team_paid(owner):
    team = Team.objects.create(name="T", owner=owner)
    assert team_is_paid(team) is False
    owner.subscription_bypass = True
    owner.save()

    assert team_is_paid(team) is True


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_bypass_allows_team_creation_through_api(owner):
    owner.subscription_bypass = True
    owner.save()

    r = _client(owner).post("/api/v1/teams/", {"name": "A"}, format="json")

    assert r.status_code == 201, r.json()


# ------------------------------------------------- réception d'un droit poussé

def _push(payload, secret="secret-de-test", timestamp=None):
    body = json.dumps(payload).encode()
    ts = timestamp if timestamp is not None else int(time.time())
    with override_settings(**BILLING_ON):
        signature = _sign("POST", "/api/v1/billing/entitlement/", body, ts)
    return APIClient().post(
        "/api/v1/billing/entitlement/",
        data=body,
        content_type="application/json",
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=signature,
    )


def _payload(user, **kwargs):
    base = {
        "delivery_id": str(uuid.uuid4()),
        "app": "poker",
        "external_user_id": str(user.id),
        "is_paid": True,
        "status": "active",
        "plan": "team1",
        "interval": "monthly",
        "quotas": {"teams": 1},
        "current_period_end": None,
        "grace_until": None,
        "stripe_customer_id": "cus_123",
        "source": "stripe",
    }
    base.update(kwargs)
    return base


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_signed_push_updates_the_local_cache(owner):
    response = _push(_payload(owner))

    assert response.status_code == 200
    sub = Subscription.objects.get(user=owner)
    assert sub.is_paid is True
    assert sub.plan == "team1"
    assert sub.quotas == {"teams": 1}
    assert sub.stripe_customer_id == "cus_123"
    assert user_is_paid(owner) is True


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unsigned_push_is_refused(owner):
    response = APIClient().post("/api/v1/billing/entitlement/", _payload(owner), format="json")

    assert response.status_code == 401
    assert Subscription.objects.count() == 0


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_push_signed_with_the_wrong_secret_is_refused(owner):
    body = json.dumps(_payload(owner)).encode()
    ts = int(time.time())
    with override_settings(BILLING_APP_SECRET="mauvais-secret"):
        signature = _sign("POST", "/api/v1/billing/entitlement/", body, ts)

    response = APIClient().post(
        "/api/v1/billing/entitlement/",
        data=body,
        content_type="application/json",
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=signature,
    )

    assert response.status_code == 401


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_stale_push_is_refused(owner):
    response = _push(_payload(owner), timestamp=int(time.time()) - 400)

    assert response.status_code == 401


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_replaying_a_delivery_is_a_no_op(owner):
    """Le central rejoue volontiers : un rejeu tardif ne doit pas réappliquer un
    état périmé par-dessus un état plus récent."""
    payload = _payload(owner)
    assert _push(payload).status_code == 200

    # L'état a évolué depuis, puis l'ancienne livraison est rejouée.
    Subscription.objects.filter(user=owner).update(is_paid=False, status="canceled")
    response = _push(payload)

    assert response.status_code == 409
    assert Subscription.objects.get(user=owner).is_paid is False


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_push_for_an_unknown_user_is_acknowledged(owner):
    """Utilisateur supprimé côté Poker : accuser réception, sinon le central
    réessaierait indéfiniment."""
    response = _push(_payload(owner, external_user_id="999999"))

    assert response.status_code == 200
    assert Subscription.objects.count() == 0


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_push_without_delivery_id_is_refused(owner):
    payload = _payload(owner)
    del payload["delivery_id"]

    response = _push(payload)

    assert response.status_code == 400


# ----------------------------------------------------- proxys vers le central

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_checkout_relays_to_the_central(owner):
    with patch("billing.client.post", return_value={"url": "https://checkout.stripe.com/c/x"}) as sent:
        response = _client(owner).post(
            "/api/v1/billing/checkout/", {"plan": "team1", "interval": "monthly"}, format="json"
        )

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://checkout.stripe.com/")
    payload = sent.call_args.args[1]
    assert payload["external_user_id"] == str(owner.id)
    assert payload["plan"] == "team1"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unreachable_central_yields_503_not_500(owner):
    """Le SPA doit voir une panne explicite, jamais une exception."""
    from billing.client import BillingUnavailable

    with patch("billing.client.post", side_effect=BillingUnavailable("timeout")):
        response = _client(owner).post(
            "/api/v1/billing/checkout/", {"plan": "team1", "interval": "monthly"}, format="json"
        )

    assert response.status_code == 503
    assert response.json()["code"] == "billing_unavailable"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_status_endpoint_never_calls_the_network_without_refresh(owner):
    _paid_sub(owner)

    with patch("billing.client.get") as called:
        body = _client(owner).get("/api/v1/billing/subscription/").json()

    called.assert_not_called()
    assert body["isPaid"] is True
    assert body["quota"] == 1


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_refresh_pulls_the_central_for_the_checkout_return(owner):
    """Sans ce pull, l'utilisateur revenant de Stripe avant le webhook verrait
    « aucun abonnement » juste après avoir payé."""
    with patch("billing.client.get", return_value=_payload(owner)) as pulled:
        body = _client(owner).get("/api/v1/billing/subscription/?refresh=1").json()

    pulled.assert_called_once()
    assert body["isPaid"] is True


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_history_degrades_to_empty_lists_when_the_central_is_down(owner):
    from billing.client import BillingUnavailable

    with patch("billing.client.get", side_effect=BillingUnavailable("timeout")):
        body = _client(owner).get("/api/v1/billing/history/").json()

    assert body == {"billingEnabled": True, "subscriptions": [], "invoices": []}


@pytest.mark.django_db
def test_history_is_empty_when_billing_is_unconfigured(owner):
    body = _client(owner).get("/api/v1/billing/history/").json()

    assert body["billingEnabled"] is False
    assert body["subscriptions"] == [] and body["invoices"] == []


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_two_different_calls_in_the_same_second_do_not_collide(owner):
    """Sans la methode et le chemin dans la signature, deux GET a corps vide emis
    dans la meme seconde produisaient une signature identique, et le central
    rejetait le second comme un rejeu. Constate le 2026-07-28."""
    from billing.client import _sign

    ts = int(time.time())
    a = _sign("GET", "/api/v1/plans/", b"", ts)
    b = _sign("GET", "/api/v1/entitlements/poker/42/", b"", ts)

    assert a != b


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_push_signed_for_another_path_is_refused(owner):
    """Une signature capturee ailleurs ne doit pas ouvrir l'endpoint de droits."""
    from billing.client import _sign

    body = json.dumps(_payload(owner)).encode()
    ts = int(time.time())
    signature = _sign("POST", "/api/v1/billing/autre-chose/", body, ts)

    response = APIClient().post(
        "/api/v1/billing/entitlement/",
        data=body,
        content_type="application/json",
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=signature,
    )

    assert response.status_code == 401


# --------------------------------------- un refus n'est pas une panne

@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_refusal_keeps_its_meaning_instead_of_becoming_an_outage(owner):
    """« Vous avez deja un abonnement » n'est pas une panne. Le presenter comme
    un service indisponible invite a reessayer un geste qui echouera toujours."""
    import requests

    reponse = requests.Response()
    reponse.status_code = 409
    reponse._content = json.dumps(
        {"code": "already_subscribed", "detail": "Un abonnement est deja en cours."}
    ).encode()

    with patch("billing.client.requests.post", return_value=reponse):
        r = _client(owner).post(
            "/api/v1/billing/checkout/", {"plan": "team1", "interval": "monthly"}, format="json"
        )

    assert r.status_code == 409
    assert r.json()["code"] == "already_subscribed"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_server_error_at_the_central_is_still_an_outage(owner):
    """Le tri doit se faire sur le code : 5xx = panne, retentable."""
    import requests

    reponse = requests.Response()
    reponse.status_code = 502
    reponse._content = b""

    with patch("billing.client.requests.post", return_value=reponse):
        r = _client(owner).post(
            "/api/v1/billing/checkout/", {"plan": "team1", "interval": "monthly"}, format="json"
        )

    assert r.status_code == 503
    assert r.json()["code"] == "billing_unavailable"


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_refused_history_still_renders_the_page(owner):
    """Sur un chemin de confort, refus et panne appellent le meme geste : servir
    ce qu'on a, plutot que de renvoyer une 500 non geree."""
    from billing.client import BillingRefused

    with patch("billing.client.get", side_effect=BillingRefused(404, "not_found", "Inconnu")):
        r = _client(owner).get("/api/v1/billing/history/")

    assert r.status_code == 200
    assert r.json()["subscriptions"] == []


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_the_api_lives_only_under_the_canonical_prefix(owner):
    """L'alias transitoire /api/ -> /api/v1/ a ete retire (OPERATIONS.md 3.18).

    Il ne couvrait que la fenetre de bascule, le temps qu'un bundle encore en
    cache cesse d'appeler l'ancien chemin.
    """
    assert _client(owner).get("/api/v1/billing/subscription/").status_code == 200
    assert _client(owner).get("/api/billing/subscription/").status_code == 404
    assert _client(owner).get("/api/v1/v1/billing/subscription/").status_code == 404


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_signed_push_on_the_canonical_path_is_accepted(owner):
    """La signature couvre le CHEMIN : le central doit pousser sur /api/v1/...
    Si l'alias reecrivait un push signe sur l'ancien chemin, la verification
    comparerait deux chemins differents et rejetterait un appel legitime."""
    r = _push(_payload(owner))

    assert r.status_code == 200


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_signed_push_on_the_legacy_path_is_refused(owner):
    """L'ancien chemin n'existe plus : l'alias transitoire a ete retire.

    Tant qu'il existait, un push signe sur l'ancien chemin etait refuse en 401 --
    la signature couvre le chemin, la reecriture le changeait, donc la
    verification comparait deux chemins differents. La consequence operationnelle
    tient toujours : `App.entitlement_path` du central DOIT pointer sur
    /api/v1/billing/entitlement/, sans quoi le push tombe desormais en 404.
    """
    payload = _payload(owner)
    body = json.dumps(payload).encode()
    ts = int(time.time())
    with override_settings(**BILLING_ON):
        signature = _sign("POST", "/api/billing/entitlement/", body, ts)

    r = APIClient().post(
        "/api/billing/entitlement/",
        data=body,
        content_type="application/json",
        HTTP_X_FOXUGLY_TIMESTAMP=str(ts),
        HTTP_X_FOXUGLY_SIGNATURE=signature,
    )

    assert r.status_code == 404


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_a_signed_ping_answers_200_so_the_console_test_means_something(db):
    """Le bouton « Test » de la console pousse {"ping": true}. Sans ce cas, un
    cablage parfait retombait sur « delivery_id requis » (400) et le test
    annoncait un echec -- pire qu'une absence de test, puisqu'on part alors
    chercher une panne qui n'existe pas."""
    r = _push({"ping": True})

    assert r.status_code == 200
    assert r.json() == {"pong": True}


@override_settings(**BILLING_ON)
@pytest.mark.django_db
def test_an_unsigned_ping_is_still_refused(db):
    """Le test doit prouver le secret, pas seulement la joignabilite."""
    r = APIClient().post("/api/v1/billing/entitlement/", {"ping": True}, format="json")

    assert r.status_code == 401
