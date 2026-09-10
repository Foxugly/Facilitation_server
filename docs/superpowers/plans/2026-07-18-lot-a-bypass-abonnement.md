# Lot A — flag `subscription_bypass` : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** un flag `subscription_bypass` sur le compte utilisateur qui accorde tous les droits payants sans souscription, administrable depuis l'admin Django et depuis un back-office staff dans les deux SPA.

**Architecture :** le flag est un booléen sur le modèle `User` de chaque site. Côté Poker il court-circuite les deux helpers existants `user_is_paid()` / `user_quota()` de `billing/service.py`, sans toucher aucun des six appelants. Côté TrainingManager, où aucune couche de droits n'existe, on crée `customuser/entitlements.py` avec la même signature et on y repointe les **trois** endroits qui recalculent le verdict de quota. Le flag est exposé en lecture seule sur `/me/`, et mutable uniquement via des endpoints `IsAdminUser` dédiés.

**Tech Stack :** Django 6 + DRF + pytest (les deux backends) ; Angular 21 standalone + signals + PrimeNG 21 + Transloco + Vitest (les deux frontends).

## Global Constraints

- **Spec de référence :** `docs/superpowers/specs/2026-07-18-bypass-abonnement-et-tables-design.md` (repo `Poker_server`).
- **Nom du champ, identique partout :** `subscription_bypass`. Champs compagnons : `bypass_note` (`CharField(max_length=200, blank=True)`), `bypass_granted_at` (`DateTimeField(null=True, blank=True)`).
- **Constante :** `UNLIMITED = 10_000` (valeur déjà utilisée en dur à `billing/service.py:63`).
- **Jamais mutable par l'utilisateur.** Tout champ exposé sur `/me/` doit figurer dans `fields` **et** `read_only_fields`. Un test d'auto-élévation par PATCH est obligatoire dans chaque backend.
- **Permission des endpoints staff :** `permission_classes = [permissions.IsAdminUser]` (donc `is_staff`). Les guards SPA restent sur `is_superuser` — l'endpoint est la frontière de sécurité, le guard n'est qu'un masquage d'UI. Ne pas « harmoniser » les deux.
- **Poker :** tests `pytest -q` ; URLs en dur dans les tests, jamais `reverse()` ; pas de fixture partagée (chaque fichier définit ses `client` / users) ; aucun linter.
- **TrainingManager :** tests `pytest -q` ; `pytestmark = pytest.mark.django_db` en tête de module ; fixtures `api_client` / `admin_client` fournies par `tests/conftest.py` ; tous les tests dans le `tests/` racine, jamais par app.
- **TrainingManager, garde de dérive OpenAPI :** toute modification de serializer impose `python manage.py spectacular --file openapi-schema.yaml --validate` et le commit du YAML **dans le même commit**. Règle permanente : 0 warning spectacular → tout nouvel endpoint porte un `@extend_schema` complet.
- **Ordre inter-repos imposé :** backend TM mergé **avant** le frontend TM. Le workflow `api-drift.yml` du front compare avec `main` du backend et rougit sinon.
- **i18n :** toute nouvelle clé doit être ajoutée dans les **cinq** catalogues `public/i18n/{fr,nl,en,it,es}.json` des deux fronts. Sur Poker, `src/app/i18n-parity.spec.ts` fait échouer le build en cas d'oubli.
- **Ne pas modifier** `tests/test_team_quota.py:114-121` côté TM : ce test asserte que `is_staff` n'accorde **aucun** bypass implicite. Le flag étant explicite et distinct, il doit rester vert tel quel — c'est un critère de non-régression.
- **Branches :** `feat/bypass-and-tables` (déjà créée sur `Poker_server`), `feat/subscription-bypass` sur les trois autres repos. Ne jamais pousser sur `main` : les quatre repos auto-déploient.

---

# Phase 1 — Backend Poker

Chemin repo : `D:\Projects\PycharmProjects\Poker_server`

### Task 1 : le champ sur le modèle User

**Files:**
- Modify: `accounts/models.py:42-62`
- Modify: `accounts/admin.py:12,17`
- Create: `accounts/migrations/0003_user_subscription_bypass.py` (généré)
- Test: `accounts/tests/test_subscription_bypass.py` (nouveau)

**Interfaces:**
- Consomme : rien.
- Produit : `User.subscription_bypass: bool`, `User.bypass_note: str`, `User.bypass_granted_at: datetime | None`.

- [ ] **Step 1 : écrire le test qui échoue**

Créer `accounts/tests/test_subscription_bypass.py` :

```python
"""Coverage of the subscription_bypass flag on the account (spec lot A).

- The field exists, defaults to False, and carries an audit note + grant date.
"""
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def test_subscription_bypass_defaults_to_false():
    user = User.objects.create_user(email="u@example.com", password="pw12345678")
    assert user.subscription_bypass is False
    assert user.bypass_note == ""
    assert user.bypass_granted_at is None


@pytest.mark.django_db
def test_subscription_bypass_is_persisted():
    user = User.objects.create_user(email="u2@example.com", password="pw12345678")
    user.subscription_bypass = True
    user.bypass_note = "early adopter"
    user.save()
    user.refresh_from_db()
    assert user.subscription_bypass is True and user.bypass_note == "early adopter"
```

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `.venv/Scripts/python -m pytest accounts/tests/test_subscription_bypass.py -q`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'subscription_bypass'`

- [ ] **Step 3 : ajouter les champs au modèle**

Dans `accounts/models.py`, après `email_confirmed` (ligne 54) et avant `USERNAME_FIELD` :

```python
    # Accès offert : accorde tous les droits payants sans souscription Stripe
    # (spec lot A). Distinct de is_staff, qui n'accorde AUCUN droit métier.
    # Court-circuité dans billing/service.py, jamais lu ailleurs.
    subscription_bypass = models.BooleanField(default=False)
    # Audit seul, aucun effet fonctionnel : pourquoi et quand l'accès a été offert.
    bypass_note = models.CharField(max_length=200, blank=True)
    bypass_granted_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4 : générer la migration**

Run: `.venv/Scripts/python manage.py makemigrations accounts`
Expected: `Migrations for 'accounts': accounts/migrations/0003_user_subscription_bypass.py` avec trois `AddField`. Ne pas écrire ce fichier à la main.

- [ ] **Step 5 : lancer le test pour vérifier qu'il passe**

Run: `.venv/Scripts/python -m pytest accounts/tests/test_subscription_bypass.py -q`
Expected: PASS, 2 passed

- [ ] **Step 6 : exposer les champs dans l'admin Django**

Dans `accounts/admin.py`, remplacer `list_display` (ligne 12) et le fieldset `"Status"` (ligne 17) :

```python
    list_display = ("email", "display_name", "is_staff", "is_superuser", "email_confirmed", "subscription_bypass")
    list_filter = ("subscription_bypass", "is_staff", "is_superuser", "is_active")
```

et remplacer la ligne du fieldset `"Status"` par :

```python
        ("Status", {"fields": ("email_confirmed",)}),
        ("Billing", {"fields": ("subscription_bypass", "bypass_note", "bypass_granted_at")}),
```

- [ ] **Step 7 : vérifier que l'admin se charge**

Run: `.venv/Scripts/python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 8 : commit**

```bash
git add accounts/models.py accounts/admin.py accounts/migrations/0003_user_subscription_bypass.py accounts/tests/test_subscription_bypass.py
git commit -m "feat(accounts): champ subscription_bypass + audit note/date"
```

---

### Task 2 : court-circuit dans les helpers de droits

**Files:**
- Modify: `billing/service.py:52-65`
- Test: `billing/tests/test_billing.py` (ajouts en fin de fichier)

**Interfaces:**
- Consomme : `User.subscription_bypass` (Task 1).
- Produit : `billing.service.UNLIMITED: int` ; `user_is_paid(user) -> bool` et `user_quota(user) -> int` honorent le bypass. `team_is_paid(team)` en hérite sans modification.

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter à la fin de `billing/tests/test_billing.py` :

```python
# ---------------------------------------------------------------- bypass (lot A)

@override_settings(STRIPE_SECRET_KEY="sk_test", STRIPE_PRICES=PRICES)
@pytest.mark.django_db
def test_bypass_grants_paid_and_unlimited_quota(owner):
    # Billing configuré, aucune souscription : sans bypass l'accès est fermé.
    assert user_is_paid(owner) is False and user_quota(owner) == 0
    owner.subscription_bypass = True
    owner.save()
    assert user_is_paid(owner) is True
    assert user_quota(owner) == 10_000


@override_settings(STRIPE_SECRET_KEY="sk_test", STRIPE_PRICES=PRICES)
@pytest.mark.django_db
def test_bypass_owner_makes_team_paid(owner):
    from billing.service import team_is_paid

    team = Team.objects.create(name="T", owner=owner)
    assert team_is_paid(team) is False
    owner.subscription_bypass = True
    owner.save()
    assert team_is_paid(team) is True


@override_settings(STRIPE_SECRET_KEY="sk_test", STRIPE_PRICES=PRICES)
@pytest.mark.django_db
def test_bypass_allows_team_creation_through_api(owner):
    owner.subscription_bypass = True
    owner.save()
    r = _client(owner).post("/api/teams/", {"name": "A"}, format="json")
    assert r.status_code == 201, r.json()
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest billing/tests/test_billing.py -q -k bypass`
Expected: FAIL — `assert False is True` sur `user_is_paid`

- [ ] **Step 3 : implémenter le court-circuit**

Dans `billing/service.py`, ajouter la constante sous `PAID_STATUSES` (ligne 8) :

```python
# Quota "illimité" : valeur haute plutôt qu'un None, pour que les comparaisons
# numériques des appelants restent valides sans cas particulier.
UNLIMITED = 10_000
```

puis remplacer intégralement `user_is_paid` et `user_quota` (lignes 52-65) :

```python
def user_is_paid(user) -> bool:
    """Whether a user may use paid features (own teams). An offered account
    (subscription_bypass) always passes. Inert (True) until Stripe is configured;
    then requires an active subscription."""
    if getattr(user, "subscription_bypass", False):
        return True
    if not billing_configured():
        return True
    return _active_subscription(user) is not None


def user_quota(user) -> int:
    """Max number of teams the user may own. Unlimited for offered accounts and
    while billing is off."""
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    if not billing_configured():
        return UNLIMITED
    sub = _active_subscription(user)
    return quota_for_plan(sub.plan) if sub else 0
```

`getattr` avec défaut plutôt qu'un accès direct : `user` peut être un `AnonymousUser` sur les vues non authentifiées.

- [ ] **Step 4 : lancer les tests pour vérifier qu'ils passent**

Run: `.venv/Scripts/python -m pytest billing/tests/test_billing.py -q`
Expected: PASS, tous les tests du fichier (anciens + 3 nouveaux)

- [ ] **Step 5 : vérifier l'absence de régression globale**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS, aucun échec

- [ ] **Step 6 : commit**

```bash
git add billing/service.py billing/tests/test_billing.py
git commit -m "feat(billing): subscription_bypass court-circuite user_is_paid/user_quota"
```

---

### Task 3 : exposition en lecture seule sur /me/ et /billing/subscription/

**Files:**
- Modify: `accounts/api_serializers.py:84-89`
- Modify: `billing/api_views.py:113-128`
- Test: `accounts/tests/test_subscription_bypass.py` (ajouts)

**Interfaces:**
- Consomme : `User.subscription_bypass` (Task 1), `user_is_paid`/`user_quota` (Task 2).
- Produit : `GET /api/auth/me/` renvoie `subscription_bypass: bool` (snake_case, read-only) ; `GET /api/billing/subscription/` renvoie `bypass: bool` (camelCase, cohérent avec `billingEnabled`/`isPaid` du même corps).

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter à `accounts/tests/test_subscription_bypass.py` :

```python
from rest_framework.test import APIClient


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.mark.django_db
def test_me_exposes_subscription_bypass():
    user = User.objects.create_user(email="me@example.com", password="pw12345678")
    user.subscription_bypass = True
    user.save()
    r = _client(user).get("/api/auth/me/")
    assert r.status_code == 200 and r.json()["subscription_bypass"] is True


@pytest.mark.django_db
def test_patch_me_cannot_self_grant_bypass():
    """Auto-élévation : le champ est read-only, un PATCH doit être ignoré."""
    user = User.objects.create_user(email="esc@example.com", password="pw12345678")
    r = _client(user).patch("/api/auth/me/", {"subscription_bypass": True}, format="json")
    assert r.status_code == 200
    user.refresh_from_db()
    assert user.subscription_bypass is False


@pytest.mark.django_db
def test_subscription_endpoint_reports_bypass():
    user = User.objects.create_user(email="sub@example.com", password="pw12345678")
    user.subscription_bypass = True
    user.save()
    r = _client(user).get("/api/billing/subscription/")
    assert r.status_code == 200 and r.json()["bypass"] is True
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest accounts/tests/test_subscription_bypass.py -q`
Expected: FAIL — `KeyError: 'subscription_bypass'`

- [ ] **Step 3 : exposer le champ sur /me/**

Dans `accounts/api_serializers.py`, remplacer le `Meta` de `UserMeSerializer` (lignes 85-89) :

```python
class UserMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # is_staff/is_superuser read-only so the SPA can gate an admin link client-side.
        # subscription_bypass read-only too: read_only_fields is what prevents a
        # PATCH /api/auth/me/ from becoming a self-elevation vector.
        fields = [
            "id", "email", "display_name", "is_active", "email_confirmed",
            "is_staff", "is_superuser", "subscription_bypass",
        ]
        read_only_fields = [
            "id", "email", "is_active", "email_confirmed",
            "is_staff", "is_superuser", "subscription_bypass",
        ]
```

Ne pas toucher `ProfileUpdateSerializer` (lignes 92-95) : il ne doit contenir que `display_name`.

- [ ] **Step 4 : exposer le flag sur l'endpoint billing**

Dans `billing/api_views.py`, ajouter une entrée au dict de `SubscriptionView.get` (après `"isPaid"`, ligne 120) :

```python
                "bypass": bool(getattr(request.user, "subscription_bypass", False)),
```

- [ ] **Step 5 : lancer les tests pour vérifier qu'ils passent**

Run: `.venv/Scripts/python -m pytest accounts/tests/ billing/tests/ -q`
Expected: PASS

- [ ] **Step 6 : commit**

```bash
git add accounts/api_serializers.py billing/api_views.py accounts/tests/test_subscription_bypass.py
git commit -m "feat(api): expose subscription_bypass en lecture seule sur /me/ et /billing/subscription/"
```

---

### Task 4 : endpoints staff

**Files:**
- Create: `accounts/api_staff_views.py`
- Create: `accounts/api_staff_urls.py`
- Modify: `config/urls.py`
- Modify: `accounts/api_serializers.py` (ajout d'un serializer)
- Test: `accounts/tests/test_staff_users.py` (nouveau)

**Interfaces:**
- Consomme : `User.subscription_bypass`, `bypass_note`, `bypass_granted_at` (Task 1).
- Produit : `GET /api/staff/users/?q=<terme>` → `{"results": [{id, email, display_name, subscription_bypass, bypass_note, bypass_granted_at}]}` (50 max) ; `PATCH /api/staff/users/<id>/` acceptant `{subscription_bypass?, bypass_note?}` → le même objet. Les deux en `IsAdminUser`.

- [ ] **Step 1 : écrire les tests qui échouent**

Créer `accounts/tests/test_staff_users.py` :

```python
"""Endpoints staff d'administration du flag subscription_bypass (spec lot A §A.4).

- Lecture et mutation réservées à is_staff (IsAdminUser).
- L'activation horodate bypass_granted_at ; la désactivation le laisse en place.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="staff@example.com", password="pw12345678", is_staff=True)


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw12345678", display_name="Mimi")


@pytest.mark.django_db
def test_staff_can_search_users(staff, member):
    r = _client(staff).get("/api/staff/users/?q=member")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1 and results[0]["email"] == "member@example.com"
    assert results[0]["subscription_bypass"] is False


@pytest.mark.django_db
def test_non_staff_cannot_search_users(member):
    r = _client(member).get("/api/staff/users/?q=member")
    assert r.status_code == 403


@pytest.mark.django_db
def test_anonymous_cannot_search_users(member):
    r = APIClient().get("/api/staff/users/?q=member")
    assert r.status_code == 401


@pytest.mark.django_db
def test_staff_grants_bypass_and_stamps_granted_at(staff, member):
    r = _client(staff).patch(
        f"/api/staff/users/{member.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    assert r.status_code == 200, r.json()
    member.refresh_from_db()
    assert member.subscription_bypass is True
    assert member.bypass_note == "asso X"
    assert member.bypass_granted_at is not None


@pytest.mark.django_db
def test_revoking_bypass_keeps_granted_at(staff, member):
    _client(staff).patch(f"/api/staff/users/{member.pk}/", {"subscription_bypass": True}, format="json")
    member.refresh_from_db()
    granted = member.bypass_granted_at
    _client(staff).patch(f"/api/staff/users/{member.pk}/", {"subscription_bypass": False}, format="json")
    member.refresh_from_db()
    assert member.subscription_bypass is False and member.bypass_granted_at == granted


@pytest.mark.django_db
def test_non_staff_cannot_grant_bypass(member):
    other = User.objects.create_user(email="other@example.com", password="pw12345678")
    r = _client(member).patch(f"/api/staff/users/{other.pk}/", {"subscription_bypass": True}, format="json")
    assert r.status_code == 403
    other.refresh_from_db()
    assert other.subscription_bypass is False
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `.venv/Scripts/python -m pytest accounts/tests/test_staff_users.py -q`
Expected: FAIL — 404 sur toutes les routes (l'URL n'existe pas)

- [ ] **Step 3 : ajouter le serializer staff**

À la fin de `accounts/api_serializers.py` :

```python
class StaffUserSerializer(serializers.ModelSerializer):
    """Vue staff d'un compte : identité + état de l'accès offert. Seuls
    subscription_bypass et bypass_note sont mutables ; bypass_granted_at est
    horodaté par la vue, jamais transmis par le client."""

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "subscription_bypass", "bypass_note", "bypass_granted_at"]
        read_only_fields = ["id", "email", "display_name", "bypass_granted_at"]
```

- [ ] **Step 4 : écrire les vues**

Créer `accounts/api_staff_views.py` :

```python
"""Administration staff des comptes (spec lot A §A.4). Surface volontairement
minimale : rechercher un compte et basculer son accès offert. Toute autre
édition passe par l'admin Django."""
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_serializers import StaffUserSerializer
from .models import User

SEARCH_LIMIT = 50


class StaffUserListView(APIView):
    """GET ?q=<terme> — recherche par email ou display_name. Sans q, renvoie
    les comptes ayant un accès offert (la liste que le staff consulte en pratique)."""

    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = User.objects.filter(Q(email__icontains=q) | Q(display_name__icontains=q))
        else:
            qs = User.objects.filter(subscription_bypass=True)
        qs = qs.order_by("email")[:SEARCH_LIMIT]
        return Response({"results": StaffUserSerializer(qs, many=True).data})


class StaffUserDetailView(APIView):
    """PATCH {subscription_bypass?, bypass_note?} — bascule l'accès offert."""

    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        was_granted = user.subscription_bypass
        serializer = StaffUserSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # Horodate à l'activation seulement : on garde la trace de l'octroi
        # initial même après une révocation.
        if user.subscription_bypass and not was_granted:
            user.bypass_granted_at = timezone.now()
            user.save(update_fields=["bypass_granted_at"])
        return Response(StaffUserSerializer(user).data)
```

- [ ] **Step 5 : câbler les URLs**

Créer `accounts/api_staff_urls.py` :

```python
from django.urls import path

from .api_staff_views import StaffUserDetailView, StaffUserListView

urlpatterns = [
    path("users/", StaffUserListView.as_view(), name="staff-user-list"),
    path("users/<int:pk>/", StaffUserDetailView.as_view(), name="staff-user-detail"),
]
```

Dans `config/urls.py`, insérer la ligne **avant** `path("api/", include("rooms.api_urls"))`, qui est un catch-all :

```python
    path("api/staff/", include("accounts.api_staff_urls")),
    path("api/", include("rooms.api_urls")),
```

- [ ] **Step 6 : lancer les tests pour vérifier qu'ils passent**

Run: `.venv/Scripts/python -m pytest accounts/tests/test_staff_users.py -q`
Expected: PASS, 6 passed

- [ ] **Step 7 : vérifier l'absence de régression globale**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS

- [ ] **Step 8 : commit**

```bash
git add accounts/api_staff_views.py accounts/api_staff_urls.py accounts/api_serializers.py config/urls.py accounts/tests/test_staff_users.py
git commit -m "feat(staff): endpoints de recherche et bascule de l'acces offert"
```

---

# Phase 2 — Backend TrainingManager

Chemin repo : `D:\Projects\PycharmProjects\trainingmanager_server`
Branche : `feat/subscription-bypass`

### Task 5 : le champ sur CustomUser

**Files:**
- Modify: `customuser/models.py` (après `team_quota`, ligne 108)
- Modify: `customuser/admin.py:20-28`
- Create: `customuser/migrations/0014_customuser_subscription_bypass.py` (généré)
- Test: `tests/test_entitlements.py` (nouveau)

**Interfaces:**
- Consomme : rien.
- Produit : `CustomUser.subscription_bypass: bool`, `bypass_note: str`, `bypass_granted_at: datetime | None`.

- [ ] **Step 1 : écrire le test qui échoue**

Créer `tests/test_entitlements.py` :

```python
"""Coverage of subscription_bypass — accès offert accordé sans souscription.

- Le champ existe, défaut False, avec note d'audit et date d'octroi.
- Il est DISTINCT de is_staff, qui n'accorde aucun droit métier
  (cf. tests/test_team_quota.py, Decision I (b) — doit rester vert).
"""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(name, **kwargs):
    return User.objects.create_user(email=f"{name}@local.test", password="Sup3rS@fePass!", **kwargs)


def test_subscription_bypass_defaults_to_false():
    user = _user("bypass_default")
    assert user.subscription_bypass is False
    assert user.bypass_note == ""
    assert user.bypass_granted_at is None
```

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `pytest tests/test_entitlements.py -q`
Expected: FAIL — `AttributeError: 'CustomUser' object has no attribute 'subscription_bypass'`

- [ ] **Step 3 : ajouter les champs**

Dans `customuser/models.py`, juste après le champ `team_quota` (fin ligne 108) :

```python
    subscription_bypass = models.BooleanField(
        default=False,
        help_text=_(
            "When True, grants every paid feature without a subscription "
            "(offered access): the team quota becomes unlimited. Distinct from "
            "is_staff, which grants no business entitlement."
        ),
    )
    bypass_note = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Audit only: why this account was offered access."),
    )
    bypass_granted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Audit only: when the access was first granted."),
    )
```

- [ ] **Step 4 : générer la migration**

Run: `python manage.py makemigrations customuser`
Expected: `customuser/migrations/0014_customuser_subscription_bypass.py`, dépendant de `0013_remove_customuser_username`, avec trois `AddField`.

- [ ] **Step 5 : lancer le test pour vérifier qu'il passe**

Run: `pytest tests/test_entitlements.py -q`
Expected: PASS, 1 passed

- [ ] **Step 6 : exposer dans l'admin Django**

Dans `customuser/admin.py`, remplacer `list_filter` (ligne 21) et ajouter un fieldset après `_("Permissions")` :

```python
    list_filter = ("subscription_bypass", "is_staff", "is_superuser", "is_active", "email_confirmed")
```

et dans `fieldsets`, après la ligne `_("Permissions")` :

```python
        (_("Billing"), {"fields": ("team_quota", "subscription_bypass", "bypass_note", "bypass_granted_at")}),
```

`team_quota` est ajouté ici au passage : le help_text du modèle annonce « admins bump this per user » alors que le champ n'était exposé nulle part dans l'admin.

- [ ] **Step 7 : vérifier que le formulaire admin accepte ces champs**

`CustomUserChangeForm` (`customuser/forms.py`) peut restreindre `fields`. L'ouvrir et vérifier : si `Meta.fields` est une liste explicite, y ajouter les quatre noms ; si c'est `"__all__"`, ne rien faire.

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 8 : commit**

```bash
git add customuser/models.py customuser/admin.py customuser/forms.py customuser/migrations/0014_customuser_subscription_bypass.py tests/test_entitlements.py
git commit -m "feat(customuser): champ subscription_bypass + expose team_quota dans l'admin"
```

---

### Task 6 : module entitlements et unification des trois call-sites

**Files:**
- Create: `customuser/entitlements.py`
- Modify: `customuser/models.py:145-146` (`can_create_team`)
- Modify: `team/views/teams.py:100-115`
- Modify: `customuser/serializers.py:81-88`
- Test: `tests/test_entitlements.py` (ajouts)

**Interfaces:**
- Consomme : `CustomUser.subscription_bypass` (Task 5).
- Produit : `customuser.entitlements.UNLIMITED: int`, `user_quota(user) -> int`, `can_create_team(user) -> bool`. Ces deux fonctions deviennent la **source unique** du verdict de quota. Pas de `user_is_paid()` : TM n'a aucun verrou de paiement à interroger, l'ajouter serait de la généralité spéculative — il viendra avec Stripe le jour venu.

**Contexte impératif :** le verdict est aujourd'hui recalculé à trois endroits (`CustomUser.can_create_team()`, l'inline de `perform_create`, l'inline de `get_team_quota`). Si un seul n'est pas repointé, `/me/` et la création de team divergeront.

- [ ] **Step 1 : écrire les tests qui échouent**

Ajouter à `tests/test_entitlements.py` :

```python
from customuser.entitlements import UNLIMITED, can_create_team, user_quota
from sport.models import Sport
from team.models import Team


@pytest.fixture
def sport(db):
    return Sport.objects.create(name="Sport Bypass", slug="sport-bypass", is_active=True)


def test_user_quota_returns_team_quota_without_bypass():
    user = _user("quota_plain", team_quota=2)
    assert user_quota(user) == 2
    assert can_create_team(user) is True


def test_user_quota_is_unlimited_with_bypass():
    user = _user("quota_bypass", team_quota=0, subscription_bypass=True)
    assert user_quota(user) == UNLIMITED
    assert can_create_team(user) is True


def test_GET_me_reports_unlimited_quota_with_bypass(api_client):
    user = _user("me_bypass", team_quota=0, subscription_bypass=True)
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/me/")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["subscription_bypass"] is True
    assert body["team_quota"] == {"used": 0, "max": UNLIMITED, "can_create": True}


def test_POST_team_with_bypass_and_zero_quota_returns_201(api_client, sport):
    user = _user("create_bypass", team_quota=0, subscription_bypass=True)
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/teams/", {"name": "Team Bypass", "sport_id": sport.pk}, format="json"
    )
    assert response.status_code == 201, response.json()
    assert Team.objects.filter(owner=user).exists()


def test_PATCH_me_cannot_self_grant_bypass(api_client):
    """Auto-élévation : le champ est read-only sur /me/."""
    user = _user("me_escalate")
    api_client.force_authenticate(user=user)
    response = api_client.patch("/api/v1/me/", {"subscription_bypass": True}, format="json")
    assert response.status_code == 200, response.json()
    user.refresh_from_db()
    assert user.subscription_bypass is False
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/test_entitlements.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'customuser.entitlements'`

- [ ] **Step 3 : écrire le module**

Créer `customuser/entitlements.py` :

```python
"""Source unique du verdict de droits payants (spec lot A §A.3).

TrainingManager n'a pas de facturation : le seul verrou est CustomUser.team_quota,
relevé à la main par un admin. Ce module existe pour que les trois appelants qui
recalculaient le verdict (le modèle, la vue de création d'équipe, /me/) partagent
la même règle, et pour que le jour où Stripe est porté ici, seul l'intérieur de
ces fonctions change.
"""

# Quota "illimité" : valeur haute plutôt qu'un None, pour que les comparaisons
# numériques des appelants restent valides sans cas particulier.
UNLIMITED = 10_000


def user_quota(user) -> int:
    """Nombre maximum d'équipes actives que l'utilisateur peut posséder."""
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    return user.team_quota


def can_create_team(user) -> bool:
    return user.active_owned_teams_count() < user_quota(user)
```

- [ ] **Step 4 : repointer le call-site n°1 — le modèle**

Dans `customuser/models.py`, remplacer `can_create_team` (lignes 145-146) :

```python
    def can_create_team(self) -> bool:
        from customuser.entitlements import can_create_team

        return can_create_team(self)
```

Import local et non en tête de module : `entitlements` n'importe rien de `models`, mais l'import local évite d'introduire une dépendance au chargement des apps.

- [ ] **Step 5 : repointer le call-site n°2 — la création d'équipe**

Dans `team/views/teams.py`, remplacer `perform_create` (lignes 100-115) :

```python
    def perform_create(self, serializer):
        user = self.request.user
        used = user.active_owned_teams_count()
        quota = user_quota(user)
        if used >= quota:
            # Enrich the exception with quota context so the response body
            # includes used/max/can_create alongside code+detail.
            exc = TeamQuotaExceeded()
            exc.detail = {
                "code": exc.default_code,
                "detail": str(exc.default_detail),
                "used": used,
                "max": quota,
                "can_create": False,
            }
            raise exc
        serializer.save(owner=user)
```

et ajouter l'import en tête du fichier, à côté des autres imports applicatifs :

```python
from customuser.entitlements import user_quota
```

- [ ] **Step 6 : repointer le call-site n°3 — /me/**

Dans `customuser/serializers.py`, remplacer `get_team_quota` (lignes 81-88) :

```python
    @extend_schema_field(TeamQuotaStatusSerializer)
    def get_team_quota(self, obj):
        used = obj.active_owned_teams_count()
        quota = user_quota(obj)
        return {
            "used": used,
            "max": quota,
            "can_create": used < quota,
        }
```

et ajouter l'import en tête :

```python
from customuser.entitlements import user_quota
```

- [ ] **Step 7 : exposer le flag en lecture seule sur /me/**

Toujours dans `customuser/serializers.py`, ajouter `"subscription_bypass"` à la liste `fields` (lignes 45-61) **et** à `read_only_fields` (lignes 68-79). Compléter le commentaire existant lignes 62-67 :

```python
        # is_staff and is_superuser are exposed READ-ONLY so the SPA can gate UI
        # affordances (the admin back-office link is superuser-only). They are the
        # user's own flags; server-side permissions still enforce every admin
        # endpoint. read_only prevents privilege escalation via PATCH.
        # subscription_bypass follows the same rule: read-only so the SPA can show
        # an "offered access" badge and hide pricing, never mutable by its owner.
```

- [ ] **Step 8 : lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/test_entitlements.py -q`
Expected: PASS, 7 passed

- [ ] **Step 9 : vérifier la non-régression du test de décision staff**

Run: `pytest tests/test_team_quota.py -q`
Expected: PASS, y compris le test qui asserte que `is_staff` n'accorde aucun bypass. Si ce test échoue, l'implémentation a confondu `is_staff` et `subscription_bypass` — corriger, ne pas modifier le test.

- [ ] **Step 10 : régénérer le schéma OpenAPI**

Le champ `subscription_bypass` sur `MeSerializer` change le contrat.

Run: `python manage.py spectacular --file openapi-schema.yaml --validate`
Expected: aucune sortie d'erreur, aucun warning. `git diff --stat openapi-schema.yaml` doit montrer une modification.

- [ ] **Step 11 : suite complète**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 12 : commit**

```bash
git add customuser/entitlements.py customuser/models.py customuser/serializers.py team/views/teams.py tests/test_entitlements.py openapi-schema.yaml
git commit -m "feat(entitlements): source unique du quota + bypass honore par les 3 call-sites"
```

---

### Task 7 : endpoints staff TrainingManager

**Files:**
- Create: `customuser/views/staff.py`
- Modify: `customuser/views/__init__.py`
- Modify: `customuser/serializers.py` (ajout `StaffUserSerializer`)
- Modify: `customuser/urls.py`
- Test: `tests/test_staff_users.py` (nouveau)

**Interfaces:**
- Consomme : `CustomUser.subscription_bypass`, `bypass_note`, `bypass_granted_at` (Task 5).
- Produit : `GET /api/v1/staff/users/?q=<terme>` et `PATCH /api/v1/staff/users/<id>/`, mêmes corps que côté Poker (Task 4), en `IsAdminUser`.

- [ ] **Step 1 : écrire les tests qui échouent**

Créer `tests/test_staff_users.py` :

```python
"""Endpoints staff d'administration du flag subscription_bypass (spec lot A §A.4).

- Réservés à is_staff : un utilisateur authentifié ordinaire reçoit 403.
- L'activation horodate bypass_granted_at ; la révocation le conserve.
"""

import pytest
from django.contrib.auth import get_user_model

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(name, **kwargs):
    return User.objects.create_user(email=f"{name}@local.test", password="Sup3rS@fePass!", **kwargs)


def test_GET_staff_users_as_admin_returns_200(admin_client):
    _user("searchable")
    response = admin_client.get("/api/v1/staff/users/?q=searchable")
    assert response.status_code == 200, response.json()
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["email"] == "searchable@local.test"
    assert results[0]["subscription_bypass"] is False


def test_GET_staff_users_as_plain_user_returns_403(auth_client):
    response = auth_client.get("/api/v1/staff/users/?q=x")
    assert response.status_code == 403, response.json()


def test_GET_staff_users_anonymous_returns_401(api_client):
    response = api_client.get("/api/v1/staff/users/?q=x")
    assert response.status_code == 401


def test_PATCH_staff_user_grants_bypass_and_stamps_date(admin_client):
    target = _user("grantee")
    response = admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    target.refresh_from_db()
    assert target.subscription_bypass is True
    assert target.bypass_note == "asso X"
    assert target.bypass_granted_at is not None


def test_PATCH_staff_user_revoke_keeps_granted_at(admin_client):
    target = _user("revokee")
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": True}, format="json"
    )
    target.refresh_from_db()
    granted = target.bypass_granted_at
    admin_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": False}, format="json"
    )
    target.refresh_from_db()
    assert target.subscription_bypass is False
    assert target.bypass_granted_at == granted


def test_PATCH_staff_user_as_plain_user_returns_403(auth_client):
    target = _user("protected")
    response = auth_client.patch(
        f"/api/v1/staff/users/{target.pk}/", {"subscription_bypass": True}, format="json"
    )
    assert response.status_code == 403, response.json()
    target.refresh_from_db()
    assert target.subscription_bypass is False
```

- [ ] **Step 2 : lancer les tests pour vérifier qu'ils échouent**

Run: `pytest tests/test_staff_users.py -q`
Expected: FAIL — 404 sur toutes les routes

- [ ] **Step 3 : ajouter le serializer**

À la fin de `customuser/serializers.py` :

```python
class StaffUserSerializer(serializers.ModelSerializer):
    """Vue staff d'un compte : identité + état de l'accès offert. Seuls
    subscription_bypass et bypass_note sont mutables ; bypass_granted_at est
    horodaté par la vue, jamais transmis par le client."""

    class Meta:
        model = CustomUser
        fields = [
            "id", "email", "first_name", "last_name",
            "subscription_bypass", "bypass_note", "bypass_granted_at",
        ]
        read_only_fields = ["id", "email", "first_name", "last_name", "bypass_granted_at"]
```

- [ ] **Step 4 : écrire les vues avec schéma explicite**

Créer `customuser/views/staff.py` :

```python
"""Administration staff des comptes (spec lot A §A.4). Surface volontairement
minimale : rechercher un compte et basculer son accès offert. Toute autre
édition passe par l'admin Django."""

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from customuser.models import CustomUser
from customuser.serializers import StaffUserSerializer

SEARCH_LIMIT = 50


class StaffUserListResponseSerializer(serializers.Serializer):
    """Enveloppe de la recherche staff (liste nue, pas de pagination)."""

    results = StaffUserSerializer(many=True)


class StaffUserListView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        summary="Search accounts (staff)",
        description=(
            "Search accounts by email or name. Without `q`, returns the accounts "
            "that currently have offered access. Staff only."
        ),
        parameters=[
            OpenApiParameter(
                name="q",
                type=str,
                required=False,
                description="Case-insensitive substring matched against email, first and last name.",
            )
        ],
        responses={200: StaffUserListResponseSerializer},
    )
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = CustomUser.objects.filter(
                Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )
        else:
            qs = CustomUser.objects.filter(subscription_bypass=True)
        qs = qs.order_by("email")[:SEARCH_LIMIT]
        return Response({"results": StaffUserSerializer(qs, many=True).data})


class StaffUserDetailView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        summary="Toggle offered access (staff)",
        description=(
            "Grant or revoke offered access on an account. Granting stamps "
            "`bypass_granted_at`; revoking keeps it, so the original grant stays "
            "auditable. Staff only."
        ),
        request=StaffUserSerializer,
        responses={200: StaffUserSerializer},
    )
    def patch(self, request, pk):
        user = get_object_or_404(CustomUser, pk=pk)
        was_granted = user.subscription_bypass
        serializer = StaffUserSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if user.subscription_bypass and not was_granted:
            user.bypass_granted_at = timezone.now()
            user.save(update_fields=["bypass_granted_at"])
        return Response(StaffUserSerializer(user).data)
```

- [ ] **Step 5 : ré-exporter les vues**

Dans `customuser/views/__init__.py`, ajouter à côté des autres ré-exports :

```python
from .staff import StaffUserDetailView, StaffUserListView  # noqa: F401
```

Si le fichier utilise une liste `__all__`, y ajouter les deux noms.

- [ ] **Step 6 : câbler les URLs**

Dans `customuser/urls.py`, ajouter deux entrées à `urlpatterns` (les préfixes sont relatifs, `api/v1/` est ajouté par l'includer) :

```python
    path("staff/users/", StaffUserListView.as_view(), name="staff_user_list"),
    path("staff/users/<int:pk>/", StaffUserDetailView.as_view(), name="staff_user_detail"),
```

et importer les deux vues en tête du fichier depuis `customuser.views`.

- [ ] **Step 7 : lancer les tests pour vérifier qu'ils passent**

Run: `pytest tests/test_staff_users.py -q`
Expected: PASS, 6 passed

- [ ] **Step 8 : régénérer le schéma sans warning**

Run: `python manage.py spectacular --file openapi-schema.yaml --validate`
Expected: aucun warning. Si spectacular signale un composant anonyme ou un type indéterminé, corriger le `@extend_schema` — la règle du repo est 0 warning permanent.

- [ ] **Step 9 : suite complète**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 10 : commit**

```bash
git add customuser/views/staff.py customuser/views/__init__.py customuser/serializers.py customuser/urls.py tests/test_staff_users.py openapi-schema.yaml
git commit -m "feat(staff): endpoints de recherche et bascule de l'acces offert"
```

---

# Phase 3 — Frontend Poker

Chemin repo : `D:\Projects\WebstormProjects\Poker_frontend`
Branche : `feat/subscription-bypass`
**Prérequis :** Phase 1 mergée et déployée (le SPA appelle les endpoints réels).

### Task 8 : masquer les surfaces tarifaires et afficher le badge

**Files:**
- Modify: `src/app/core/billing/billing.service.ts:10-19` (interface `SubscriptionStatus`)
- Create: `src/app/core/billing/gating.ts`
- Modify: `src/app/core/auth/auth.models.ts:1-8` (interface `AuthUser`)
- Modify: `src/app/features/teams/teams-list.component.ts:129-134` (+ template)
- Modify: `src/app/features/pricing/pricing.component.ts` (+ template)
- Modify: `public/i18n/{fr,nl,en,it,es}.json`
- Test: `src/app/core/billing/gating.spec.ts` (nouveau)

**Interfaces:**
- Consomme : `GET /api/billing/subscription/` renvoie `bypass: boolean` (Task 3) ; `GET /api/auth/me/` renvoie `subscription_bypass: boolean` (Task 3).
- Produit : `SubscriptionStatus.bypass: boolean` ; `AuthUser.subscription_bypass: boolean` ; `needsSubscription(s)` et `quotaReached(s)` exportés depuis `core/billing/gating.ts`.

**Note :** Poker n'a pas de page Profil (le seul point utilisateur est `core/layout/user-menu/`, qui ne contient que la déconnexion). Le badge « Accès offert » va donc dans le bloc abonnement de `teams-list`, là où le statut est déjà affiché. Créer une page Profil est hors périmètre du lot A.

- [ ] **Step 1 : écrire le test qui échoue**

Créer `src/app/core/billing/gating.spec.ts`. Suivre le patron du repo : fonctions pures importées, pas de TestBed (aucun TestBed n'existe dans ce projet). Le test importe les règles que le composant utilisera réellement — ne jamais redéfinir la logique dans le spec, sinon le test valide une copie et non le code livré.

```ts
import { describe, expect, it } from 'vitest';
import { SubscriptionStatus } from './billing.service';
import { needsSubscription, quotaReached } from './gating';

const base: SubscriptionStatus = {
  billingEnabled: true, isPaid: false, status: '', plan: '', interval: '',
  quota: 0, teamsUsed: 3, canManage: false, bypass: false,
};

describe('gating tarifaire', () => {
  it('demande un abonnement quand le billing est actif et le compte non payant', () => {
    expect(needsSubscription(base)).toBe(true);
    expect(quotaReached(base)).toBe(true);
  });

  it('ne demande rien quand le compte a un acces offert', () => {
    const offered = { ...base, bypass: true };
    expect(needsSubscription(offered)).toBe(false);
    expect(quotaReached(offered)).toBe(false);
  });
});
```

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test`
Expected: FAIL — module `./gating` introuvable, et `bypass` n'existe pas sur `SubscriptionStatus`

- [ ] **Step 3 : étendre les deux interfaces**

Dans `src/app/core/billing/billing.service.ts`, ajouter au champ de l'interface `SubscriptionStatus` :

```ts
  /** Accès offert : le compte a tous les droits payants sans souscription. */
  bypass: boolean;
```

Dans `src/app/core/auth/auth.models.ts`, ajouter à `AuthUser` (snake_case : le payload DRF n'est pas remappé) :

```ts
  subscription_bypass: boolean;
```

- [ ] **Step 4 : écrire le module de gating**

Créer `src/app/core/billing/gating.ts` :

```ts
import { SubscriptionStatus } from './billing.service';

/** Règles d'affichage des surfaces tarifaires. Fonctions pures, partagées par
 *  teams-list et /pricing, et testées directement (le repo n'a pas de TestBed).
 *  Un accès offert (bypass) neutralise les deux verrous. */

export function needsSubscription(s: SubscriptionStatus | null): boolean {
  return !!s && !s.bypass && s.billingEnabled === true && s.isPaid === false;
}

export function quotaReached(s: SubscriptionStatus | null): boolean {
  return !!s && !s.bypass && s.billingEnabled && s.teamsUsed >= s.quota;
}
```

- [ ] **Step 5 : brancher le composant sur le module**

Dans `src/app/features/teams/teams-list.component.ts`, remplacer les deux computeds (lignes 129-133) par une délégation — la logique ne doit exister qu'à un seul endroit :

```ts
  readonly needsSubscription = computed(() => needsSubscription(this.sub()));
  readonly quotaReached = computed(() => quotaReached(this.sub()));
```

et importer les deux fonctions :

```ts
import { needsSubscription, quotaReached } from '../../core/billing/gating';
```

Renommer les imports si le compilateur signale un conflit avec les propriétés de classe (`import { needsSubscription as needsSubscriptionRule, ... }`).

`canCreate` (ligne 134) dérive des deux et n'a pas à changer.

- [ ] **Step 6 : afficher le badge**

Dans le template de `teams-list.component.ts`, à l'endroit où le statut d'abonnement est rendu (le bloc gouverné par `needsSubscription()`, autour de la ligne 52), ajouter avant ce bloc :

```html
@if (sub()?.bypass) {
  <p-tag severity="success" icon="pi pi-gift" [value]="'billing.offered_access' | transloco" />
}
```

Ajouter `Tag` aux `imports` du composant (`import { Tag } from 'primeng/tag';`) s'il n'y figure pas déjà.

Le composant partagé `app-status-badge` n'est pas réutilisé ici : son vocabulaire `active`/`inactive` (icônes check-circle / ban) ne convient pas, et l'élargir toucherait trois écrans admin sans bénéfice.

- [ ] **Step 7 : masquer la page /pricing**

Dans `src/app/features/pricing/pricing.component.ts`, injecter `BillingService`, exposer le statut, et envelopper la grille tarifaire du template :

```ts
  private readonly billing = inject(BillingService);
  protected readonly sub = signal<SubscriptionStatus | null>(null);

  ngOnInit(): void {
    this.billing.status().subscribe({ next: (s) => this.sub.set(s), error: () => {} });
  }
```

et dans le template, autour de la grille de plans :

```html
@if (sub()?.bypass) {
  <p-message severity="success" [text]="'billing.offered_access_hint' | transloco" />
} @else {
  <!-- grille tarifaire existante, inchangée -->
}
```

- [ ] **Step 8 : ajouter les clés i18n dans les cinq catalogues**

Dans chacun de `public/i18n/{fr,nl,en,it,es}.json`, section `billing` :

```json
"offered_access": "Accès offert",
"offered_access_hint": "Votre compte bénéficie d'un accès offert : aucun abonnement n'est nécessaire."
```

Traductions à poser dans les cinq fichiers :

| Langue | `offered_access` | `offered_access_hint` |
|---|---|---|
| fr | Accès offert | Votre compte bénéficie d'un accès offert : aucun abonnement n'est nécessaire. |
| en | Offered access | Your account has offered access: no subscription is required. |
| nl | Gratis toegang | Je account heeft gratis toegang: er is geen abonnement nodig. |
| it | Accesso offerto | Il tuo account ha un accesso offerto: nessun abbonamento è necessario. |
| es | Acceso gratuito | Tu cuenta tiene acceso gratuito: no se necesita ninguna suscripción. |

- [ ] **Step 9 : lancer les tests et le build**

Run: `npm test`
Expected: PASS, y compris `src/app/i18n-parity.spec.ts` — il échoue si une clé manque dans l'un des cinq catalogues.

Run: `npm run build`
Expected: build réussi, aucune erreur TypeScript

- [ ] **Step 10 : commit**

```bash
git add src/app/core/billing/ src/app/core/auth/auth.models.ts src/app/features/teams/ src/app/features/pricing/ public/i18n/
git commit -m "feat(billing): masque les tarifs et affiche le badge acces offert"
```

---

### Task 9 : back-office staff Poker

**Files:**
- Create: `src/app/core/auth/superuser.guard.ts`
- Create: `src/app/core/staff/staff.service.ts`
- Create: `src/app/core/staff/staff.models.ts`
- Create: `src/app/features/staff/staff-users.component.ts`
- Modify: `src/app/app.routes.ts`
- Modify: `src/app/core/layout/user-menu/user-menu.component.html`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`
- Test: `src/app/core/staff/staff.service.spec.ts`

**Interfaces:**
- Consomme : `GET /api/staff/users/?q=` et `PATCH /api/staff/users/<id>/` (Task 4) ; `AuthUser.is_superuser` (existant).
- Produit : `superuserGuard: CanActivateFn` ; `StaffUser` ; `StaffService.search(q)` / `StaffService.setBypass(id, bypass, note)`.

- [ ] **Step 1 : écrire le guard**

Créer `src/app/core/auth/superuser.guard.ts`, calqué sur `auth.guard.ts` du même dossier :

```ts
import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';

/** Guards the staff back-office; sends non-superusers home. UI masking only —
 *  the server enforces IsAdminUser on every staff endpoint. */
export const superuserGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (!auth.isAuthenticated()) {
    return router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
  }
  if (!auth.currentUser()?.is_superuser) return router.createUrlTree(['/']);
  return true;
};
```

- [ ] **Step 2 : écrire le test du service qui échoue**

Créer `src/app/core/staff/staff.service.spec.ts` :

```ts
import { describe, expect, it, vi } from 'vitest';
import { of } from 'rxjs';
import { StaffService } from './staff.service';

function serviceWith(http: { get: unknown; patch: unknown }) {
  return new StaffService(http as never);
}

describe('StaffService', () => {
  it('search passe le terme en parametre q', () => {
    const get = vi.fn().mockReturnValue(of({ results: [] }));
    serviceWith({ get, patch: vi.fn() }).search('mimi').subscribe();
    expect(get).toHaveBeenCalledWith('/api/staff/users/', { params: { q: 'mimi' } });
  });

  it('setBypass envoie le flag et la note', () => {
    const patch = vi.fn().mockReturnValue(of({}));
    serviceWith({ get: vi.fn(), patch }).setBypass(7, true, 'asso X').subscribe();
    expect(patch).toHaveBeenCalledWith('/api/staff/users/7/', {
      subscription_bypass: true,
      bypass_note: 'asso X',
    });
  });
});
```

- [ ] **Step 3 : lancer le test pour vérifier qu'il échoue**

Run: `npm test`
Expected: FAIL — module `./staff.service` introuvable

- [ ] **Step 4 : écrire le modèle et le service**

Créer `src/app/core/staff/staff.models.ts` :

```ts
export interface StaffUser {
  id: number;
  email: string;
  display_name: string;
  subscription_bypass: boolean;
  bypass_note: string;
  bypass_granted_at: string | null;
}
```

Créer `src/app/core/staff/staff.service.ts`, sur le modèle des autres services du dossier `core/` (wrappers `HttpClient` écrits à la main — ce repo n'a pas de génération OpenAPI) :

```ts
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, map } from 'rxjs';
import { StaffUser } from './staff.models';

/** Back-office staff : recherche de comptes et bascule de l'accès offert.
 *  Le serveur applique IsAdminUser ; le guard côté client n'est qu'un masquage. */
@Injectable({ providedIn: 'root' })
export class StaffService {
  // Injection par constructeur (et non le champ `inject()` habituel) : le repo
  // n'a pas de TestBed, le spec instancie le service avec un mock via `new`.
  constructor(private readonly http: HttpClient) {}

  search(q: string): Observable<StaffUser[]> {
    return this.http
      .get<{ results: StaffUser[] }>('/api/staff/users/', { params: { q } })
      .pipe(map((r) => r.results));
  }

  setBypass(id: number, bypass: boolean, note: string): Observable<StaffUser> {
    return this.http.patch<StaffUser>(`/api/staff/users/${id}/`, {
      subscription_bypass: bypass,
      bypass_note: note,
    });
  }
}
```

- [ ] **Step 5 : lancer le test pour vérifier qu'il passe**

Run: `npm test`
Expected: PASS, 2 nouveaux tests

- [ ] **Step 6 : écrire l'écran**

Créer `src/app/features/staff/staff-users.component.ts`. Conventions du repo : standalone, template inline, signals, `FormsModule` + `[(ngModel)]` (aucun `ReactiveFormsModule` dans ce projet), PrimeNG, classes SCSS simples contre les tokens de `src/styles/_tokens.scss`.

```ts
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe, TranslocoService } from '@jsverse/transloco';
import { MessageService } from 'primeng/api';
import { Button } from 'primeng/button';
import { InputText } from 'primeng/inputtext';
import { TableModule } from 'primeng/table';
import { Tag } from 'primeng/tag';
import { ToggleSwitch } from 'primeng/toggleswitch';
import { StaffService } from '../../core/staff/staff.service';
import { StaffUser } from '../../core/staff/staff.models';

@Component({
  selector: 'app-staff-users',
  standalone: true,
  imports: [FormsModule, TableModule, Button, InputText, Tag, ToggleSwitch, TranslocoPipe],
  template: `
    <div class="staff-users">
      <h1 class="staff-users__title">{{ 'staff.users.title' | transloco }}</h1>

      <div class="staff-users__search">
        <input
          pInputText
          [(ngModel)]="query"
          [placeholder]="'staff.users.search_placeholder' | transloco"
          (keyup.enter)="search()"
        />
        <p-button
          [label]="'staff.users.search' | transloco"
          icon="pi pi-search"
          [loading]="loading()"
          (onClick)="search()"
        />
      </div>

      <p-table [value]="users()" [loading]="loading()">
        <ng-template pTemplate="header">
          <tr>
            <th>{{ 'staff.users.fields.email' | transloco }}</th>
            <th>{{ 'staff.users.fields.name' | transloco }}</th>
            <th>{{ 'staff.users.fields.bypass' | transloco }}</th>
            <th>{{ 'staff.users.fields.note' | transloco }}</th>
          </tr>
        </ng-template>
        <ng-template pTemplate="body" let-user>
          <tr>
            <td>{{ user.email }}</td>
            <td>{{ user.display_name }}</td>
            <td>
              <p-toggleswitch
                [ngModel]="user.subscription_bypass"
                (ngModelChange)="toggle(user, $event)"
                [disabled]="busy() === user.id"
              />
              @if (user.subscription_bypass) {
                <p-tag severity="success" icon="pi pi-gift" [value]="'billing.offered_access' | transloco" />
              }
            </td>
            <td>{{ user.bypass_note }}</td>
          </tr>
        </ng-template>
        <ng-template pTemplate="emptymessage">
          <tr>
            <td colspan="4" class="staff-users__empty">—</td>
          </tr>
        </ng-template>
      </p-table>
    </div>
  `,
  styles: [
    `
      :host { display: block; }
      .staff-users__title { font-size: 1.5rem; font-weight: 700; color: var(--ink); margin-bottom: var(--s-4); }
      .staff-users__search { display: flex; gap: var(--s-2); margin-bottom: var(--s-4); }
      .staff-users__empty { text-align: center; padding-block: var(--s-4); color: var(--muted); }
    `,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StaffUsersComponent {
  private readonly staff = inject(StaffService);
  private readonly messages = inject(MessageService);
  private readonly transloco = inject(TranslocoService);

  protected query = '';
  protected readonly users = signal<StaffUser[]>([]);
  protected readonly loading = signal(false);
  protected readonly busy = signal<number | null>(null);

  protected search(): void {
    this.loading.set(true);
    this.staff.search(this.query).subscribe({
      next: (users) => {
        this.users.set(users);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.messages.add({
          severity: 'error',
          detail: this.transloco.translate('staff.users.errors.load_failed'),
        });
      },
    });
  }

  protected toggle(user: StaffUser, next: boolean): void {
    this.busy.set(user.id);
    this.staff.setBypass(user.id, next, user.bypass_note).subscribe({
      next: (updated) => {
        this.users.update((list) => list.map((u) => (u.id === updated.id ? updated : u)));
        this.busy.set(null);
        this.messages.add({
          severity: 'success',
          detail: this.transloco.translate('staff.users.actions.saved'),
        });
      },
      error: () => {
        this.busy.set(null);
        this.messages.add({
          severity: 'error',
          detail: this.transloco.translate('staff.users.errors.save_failed'),
        });
      },
    });
  }
}
```

- [ ] **Step 7 : déclarer la route**

Dans `src/app/app.routes.ts`, ajouter dans le tableau `children` du `PublicLayoutComponent`, **avant** le `{ path: '**' }` final :

```ts
{
  path: 'staff/users',
  canActivate: [superuserGuard],
  loadComponent: () =>
    import('./features/staff/staff-users.component').then((m) => m.StaffUsersComponent),
},
```

et importer `superuserGuard` en tête du fichier.

- [ ] **Step 8 : ajouter le lien de navigation**

Dans `src/app/core/layout/user-menu/user-menu.component.html`, avant l'entrée de déconnexion :

```html
@if (currentUser()?.is_superuser) {
  <a class="user-menu__item" [routerLink]="['/staff/users']">
    {{ 'staff.nav.users' | transloco }}
  </a>
}
```

Vérifier que le composant expose `currentUser` et importe `RouterLink` et `TranslocoPipe` ; les ajouter sinon.

- [ ] **Step 9 : ajouter les clés i18n dans les cinq catalogues**

Nouvelle section racine `staff` dans `public/i18n/{fr,nl,en,it,es}.json`. Version française :

```json
"staff": {
  "nav": { "users": "Comptes" },
  "users": {
    "title": "Administration des comptes",
    "search": "Rechercher",
    "search_placeholder": "Email ou nom",
    "fields": {
      "email": "Email",
      "name": "Nom",
      "bypass": "Accès offert",
      "note": "Motif"
    },
    "actions": { "saved": "Compte mis à jour." },
    "errors": {
      "load_failed": "Échec du chargement des comptes.",
      "save_failed": "Échec de la mise à jour du compte."
    }
  }
}
```

Traduire à l'identique dans les quatre autres catalogues — la parité de clés est vérifiée par `i18n-parity.spec.ts`, qui casse le build en cas d'écart.

- [ ] **Step 10 : tests et build**

Run: `npm test`
Expected: PASS, parité i18n incluse

Run: `npm run build`
Expected: build réussi

- [ ] **Step 11 : commit**

```bash
git add src/app/core/auth/superuser.guard.ts src/app/core/staff/ src/app/features/staff/ src/app/app.routes.ts src/app/core/layout/user-menu/ public/i18n/
git commit -m "feat(staff): back-office de gestion de l'acces offert"
```

---

# Phase 4 — Frontend TrainingManager

Chemin repo : `D:\Projects\WebstormProjects\trainingmanager_frontend`
Branche : `feat/subscription-bypass`
**Prérequis strict :** Phase 2 mergée sur `main` du backend. Le workflow `api-drift.yml` tourne sur chaque PR et chaque push de branche ; il rougira tant que le schéma amont ne contient pas `subscription_bypass`.

### Task 10 : re-vendorer le schéma et régénérer le client

**Files:**
- Modify: `openapi/Training_Manager_API.yaml`
- Modify: `src/app/api/**` (généré)

**Interfaces:**
- Consomme : `openapi-schema.yaml` de `trainingmanager_server@main` (Tasks 6 et 7).
- Produit : `Me.subscription_bypass: boolean` (readonly) dans `src/app/api/model/me.ts` ; un service généré exposant les endpoints staff ; `TeamQuotaStatus` inchangé dans sa forme.

- [ ] **Step 1 : copier le schéma amont**

Depuis la racine du repo frontend, avec le backend à jour en local sur `main` :

```bash
cp ../../PycharmProjects/trainingmanager_server/openapi-schema.yaml openapi/Training_Manager_API.yaml
```

Adapter le chemin relatif si nécessaire. Le fichier vendored est en CRLF ; ne pas le normaliser, le garde CI compare avec `--ignore-cr-at-eol`.

- [ ] **Step 2 : régénérer le client**

Requiert **Java 17** (le CLI openapi-generator lance un jar).

Run: `npm run api:gen`
Expected: régénération de `src/app/api/`. `git status` doit montrer au minimum `src/app/api/model/me.ts` modifié et de nouveaux fichiers pour les endpoints staff.

- [ ] **Step 3 : vérifier que le champ est bien présent**

Run: `grep -n "subscription_bypass" src/app/api/model/me.ts`
Expected: une ligne `readonly subscription_bypass: boolean;`

Si la ligne est absente, le schéma copié est périmé : reprendre au Step 1 après avoir vérifié que la Phase 2 est bien mergée.

- [ ] **Step 4 : vérifier la compilation**

Run: `npm run build`
Expected: build réussi

- [ ] **Step 5 : commit**

```bash
git add openapi/ src/app/api/
git commit -m "chore(api): re-vendorage du schema + regeneration du client (subscription_bypass)"
```

---

### Task 11 : badge « Accès offert » sur la page Profil

**Files:**
- Modify: `src/app/features/profile/profile.component.ts` (imports)
- Modify: `src/app/features/profile/profile.component.html:1-10`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`
- Test: `src/app/features/profile/profile.component.spec.ts` (ajout)

**Interfaces:**
- Consomme : `Me.subscription_bypass` (Task 10).
- Produit : rien pour les tâches suivantes.

- [ ] **Step 1 : écrire le test qui échoue**

Ajouter au `describe` existant de `src/app/features/profile/profile.component.spec.ts`. Le repo neutralise le template (`overrideComponent({ template: '' })`) et accède aux membres `protected` via une interface locale — suivre ce patron plutôt que d'assertir sur le DOM.

```ts
it('expose le flag acces offert du compte', () => {
  const me = { ...baseMe, subscription_bypass: true };
  access(component).hydrate(me);
  expect(access(component).user()?.subscription_bypass).toBe(true);
});
```

Ajouter `hydrate(me: Me): void;` à l'interface `ProtectedFields` du fichier si elle ne l'expose pas déjà, et `subscription_bypass: false` à l'objet `baseMe` du spec.

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test -- profile`
Expected: FAIL — erreur TypeScript si `baseMe` n'a pas le champ, ou assertion en échec

- [ ] **Step 3 : afficher le badge**

Dans `src/app/features/profile/profile.component.html`, dans l'`app-page-header` (lignes 2-7). Le slot `title-after` est documenté dans `page-header.component.ts` comme l'emplacement canonique d'un badge de statut :

```html
  <app-page-header [title]="'profile.title' | transloco">
    <p-button slot="left" type="button" severity="secondary" [outlined]="true"
              icon="pi pi-arrow-left" [label]="'common.back' | transloco"
              [routerLink]="['/dashboard']" />
    @if (user()?.subscription_bypass) {
      <p-tag slot="title-after" severity="success" icon="pi pi-gift"
             [value]="'profile.offered_access' | transloco" />
    }
  </app-page-header>
```

Ajouter `Tag` aux `imports` du composant : `import { Tag } from 'primeng/tag';`.

Ne pas réutiliser `app-status-badge` : son type `StatusBadgeKind` est binaire `active`/`inactive` et l'élargir toucherait les trois écrans admin qui en dépendent.

- [ ] **Step 4 : ajouter la clé i18n dans les cinq catalogues**

Section `profile` de `public/i18n/{fr,nl,en,it,es}.json` :

| Langue | `profile.offered_access` |
|---|---|
| fr | Accès offert |
| en | Offered access |
| nl | Gratis toegang |
| it | Accesso offerto |
| es | Acceso gratuito |

- [ ] **Step 5 : lancer les tests et le build**

Run: `npm test`
Expected: PASS

Run: `npm run build`
Expected: build réussi

- [ ] **Step 6 : commit**

```bash
git add src/app/features/profile/ public/i18n/
git commit -m "feat(profile): badge acces offert dans l'en-tete de page"
```

---

### Task 12 : écran back-office « Comptes »

**Files:**
- Create: `src/app/features/admin/users/users-list/users-list.component.ts`
- Create: `src/app/features/admin/users/users-list/users-list.component.html`
- Create: `src/app/features/admin/users/users-list/users-list.component.scss`
- Create: `src/app/features/admin/users/users-list/users-list.component.spec.ts`
- Modify: `src/app/app.routes.ts:138-177`
- Modify: `src/app/core/layout/admin-layout/admin-layout.component.html`
- Modify: `public/i18n/{fr,nl,en,it,es}.json`

**Interfaces:**
- Consomme : le service staff généré (Task 10) ; `superuserGuard` (existant, `core/auth/superuser.guard.ts`).
- Produit : route `/admin/users`.

**Note d'architecture :** ne pas hériter de `TaxonomyListBase` (`features/admin/shared/taxonomy-list.base.ts`). Elle impose `destroyOne`/`restoreOne` et un cycle actif/inactif propre au CRUD de taxonomie, sans rapport avec une recherche de comptes. Écrire un composant autonome sur le modèle visuel de `SportsListComponent`.

Les pages admin de ce repo n'utilisent **pas** `app-page-header` : elles ont leur propre `__head`. Suivre cette convention locale.

- [ ] **Step 1 : écrire le test qui échoue**

Créer `src/app/features/admin/users/users-list/users-list.component.spec.ts`, sur le patron de `sports-list.component.spec.ts` : `overrideComponent` avec `template: ''`, mocks de service, accès aux membres protégés via une interface locale.

```ts
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffService } from '../../../../api/api/staff.service';
import { UsersListComponent } from './users-list.component';

interface ProtectedFields {
  users(): { id: number; email: string; subscription_bypass: boolean }[];
  loading(): boolean;
  query: string;
  search(): void;
  toggle(user: { id: number; bypass_note: string }, next: boolean): void;
}
const access = (c: UsersListComponent) => c as unknown as ProtectedFields;

describe('UsersListComponent', () => {
  let fixture: ComponentFixture<UsersListComponent>;
  let component: UsersListComponent;
  const staffMock = {
    staffUsersList: vi.fn().mockReturnValue(of({ results: [{ id: 1, email: 'a@b.c', subscription_bypass: false, bypass_note: '' }] })),
    staffUsersPartialUpdate: vi.fn().mockReturnValue(of({ id: 1, email: 'a@b.c', subscription_bypass: true, bypass_note: '' })),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [UsersListComponent] })
      .overrideComponent(UsersListComponent, {
        set: { template: '', imports: [], providers: [{ provide: StaffService, useValue: staffMock }] },
      })
      .compileComponents();
    fixture = TestBed.createComponent(UsersListComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('recherche les comptes avec le terme saisi', () => {
    access(component).query = 'mimi';
    access(component).search();
    expect(staffMock.staffUsersList).toHaveBeenCalled();
    expect(access(component).users().length).toBe(1);
  });

  it('bascule l acces offert et met a jour la ligne', () => {
    access(component).search();
    access(component).toggle({ id: 1, bypass_note: '' }, true);
    expect(staffMock.staffUsersPartialUpdate).toHaveBeenCalled();
    expect(access(component).users()[0].subscription_bypass).toBe(true);
  });
});
```

Les noms exacts des méthodes générées (`staffUsersList`, `staffUsersPartialUpdate`) et le nom du service (`StaffService`) dépendent des `operationId` produits par spectacular à la Task 7. Les relever dans `src/app/api/api/` après la Task 10 et ajuster le spec **et** le composant en conséquence — ne pas deviner.

- [ ] **Step 2 : lancer le test pour vérifier qu'il échoue**

Run: `npm test -- users-list`
Expected: FAIL — composant introuvable

- [ ] **Step 3 : écrire le composant**

Créer les trois fichiers, en suivant `sports-list.component.*` : standalone, `OnPush`, `templateUrl`/`styleUrl` séparés, BEM strict dans le SCSS (préfixe `.users-list__`), tokens uniquement pour les couleurs.

`users-list.component.ts` :

```ts
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslocoPipe, TranslocoService } from '@jsverse/transloco';
import { Button } from 'primeng/button';
import { InputText } from 'primeng/inputtext';
import { Message } from 'primeng/message';
import { TableModule } from 'primeng/table';
import { Tag } from 'primeng/tag';
import { ToggleSwitch } from 'primeng/toggleswitch';
import { StaffService } from '../../../../api/api/staff.service';
import { StaffUser } from '../../../../api/model/staff-user';
import { ToastService } from '../../../../core/messages/toast.service';

@Component({
  selector: 'app-users-list',
  imports: [FormsModule, TableModule, Button, InputText, Message, Tag, ToggleSwitch, TranslocoPipe],
  templateUrl: './users-list.component.html',
  styleUrl: './users-list.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class UsersListComponent {
  private readonly staffService = inject(StaffService);
  private readonly toast = inject(ToastService);
  private readonly transloco = inject(TranslocoService);

  protected query = '';
  protected readonly users = signal<StaffUser[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal(false);
  protected readonly busy = signal<number | null>(null);

  constructor() {
    this.search();
  }

  protected search(): void {
    this.loading.set(true);
    this.error.set(false);
    this.staffService.staffUsersList({ q: this.query || undefined }).subscribe({
      next: (response) => {
        this.users.set(response.results);
        this.loading.set(false);
      },
      error: () => {
        this.error.set(true);
        this.loading.set(false);
      },
    });
  }

  protected toggle(user: StaffUser, next: boolean): void {
    this.busy.set(user.id);
    this.staffService
      .staffUsersPartialUpdate({
        id: user.id,
        patchedStaffUserRequest: { subscription_bypass: next, bypass_note: user.bypass_note },
      })
      .subscribe({
        next: (updated) => {
          this.users.update((list) => list.map((u) => (u.id === updated.id ? updated : u)));
          this.busy.set(null);
          this.toast.success('admin.users.actions.saved');
        },
        error: () => {
          this.busy.set(null);
          this.toast.error('admin.users.errors.save_failed');
        },
      });
  }
}
```

Vérifier le chemin et l'API exacte de `ToastService` dans `src/app/core/messages/` : la convention du repo est de lui passer la **clé** i18n non traduite (`this.toast.success('profile.saved')`), pas la chaîne traduite.

`users-list.component.html` :

```html
<div class="users-list__head">
  <h1 class="users-list__title">{{ 'admin.users.title' | transloco }}</h1>
</div>

<div class="users-list__search">
  <input pInputText [(ngModel)]="query" [placeholder]="'admin.users.search_placeholder' | transloco"
         (keyup.enter)="search()" />
  <p-button [label]="'admin.users.search' | transloco" icon="pi pi-search"
            [loading]="loading()" (onClick)="search()" />
</div>

<p-table [value]="users()" [loading]="loading()" [paginator]="true" [rows]="20"
         styleClass="users-list__table">
  <ng-template pTemplate="header">
    <tr>
      <th>{{ 'admin.users.fields.email' | transloco }}</th>
      <th>{{ 'admin.users.fields.name' | transloco }}</th>
      <th>{{ 'admin.users.fields.bypass' | transloco }}</th>
      <th>{{ 'admin.users.fields.note' | transloco }}</th>
    </tr>
  </ng-template>
  <ng-template pTemplate="body" let-user>
    <tr>
      <td>{{ user.email }}</td>
      <td>{{ user.first_name }} {{ user.last_name }}</td>
      <td class="users-list__bypass">
        <p-toggleswitch [ngModel]="user.subscription_bypass"
                        (ngModelChange)="toggle(user, $event)"
                        [disabled]="busy() === user.id" />
        @if (user.subscription_bypass) {
          <p-tag severity="success" icon="pi pi-gift"
                 [value]="'admin.users.fields.bypass' | transloco" />
        }
      </td>
      <td>{{ user.bypass_note }}</td>
    </tr>
  </ng-template>
  <ng-template pTemplate="emptymessage">
    <tr>
      @if (error()) {
        <td colspan="4" class="users-list__empty-cell">
          <div class="users-list__empty-error">
            <p-message severity="error" [text]="'common.load_failed' | transloco" />
            <p-button [label]="'common.retry' | transloco" icon="pi pi-refresh"
                      severity="secondary" [outlined]="true" size="small" (onClick)="search()" />
          </div>
        </td>
      } @else {
        <td colspan="4" class="users-list__empty-cell">—</td>
      }
    </tr>
  </ng-template>
</p-table>
```

`users-list.component.scss` :

```scss
:host { display: block; }
.users-list__head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem; }
.users-list__title { font-size: 1.5rem; font-weight: 700; }
.users-list__search { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.users-list__table { border-radius: var(--radius-sm); box-shadow: 0 1px 2px rgb(0 0 0 / 5%); }
.users-list__bypass { display: flex; align-items: center; gap: 0.5rem; }
.users-list__empty-cell { text-align: center; padding-block: 1rem; color: var(--muted); }
.users-list__empty-error { display: inline-flex; flex-direction: column; align-items: center; gap: 0.5rem; }
```

- [ ] **Step 4 : lancer le test pour vérifier qu'il passe**

Run: `npm test -- users-list`
Expected: PASS, 2 passed

- [ ] **Step 5 : déclarer la route**

Dans `src/app/app.routes.ts`, ajouter un cinquième enfant au bloc `admin` (après `modalities`, ligne ~172) :

```ts
        {
          path: 'users',
          loadComponent: () =>
            import('./features/admin/users/users-list/users-list.component').then(
              (m) => m.UsersListComponent,
            ),
        },
```

Aucun guard à ajouter : le bloc parent porte déjà `canActivate: [authGuard, superuserGuard]`.

- [ ] **Step 6 : ajouter le lien dans la sidebar admin**

Dans `src/app/core/layout/admin-layout/admin-layout.component.html`, après le lien `modalities` :

```html
      <a [routerLink]="['/admin/users']" routerLinkActive="admin-shell__link--active" class="admin-shell__link">
        {{ 'admin.nav.users' | transloco }}
      </a>
```

- [ ] **Step 7 : ajouter les clés i18n dans les cinq catalogues**

Dans la section `admin` de `public/i18n/{fr,nl,en,it,es}.json` : `admin.nav.users`, et un bloc `admin.users`. Version française :

```json
"users": {
  "title": "Administration des comptes",
  "search": "Rechercher",
  "search_placeholder": "Email ou nom",
  "fields": {
    "email": "Email",
    "name": "Nom",
    "bypass": "Accès offert",
    "note": "Motif"
  },
  "actions": { "saved": "Compte mis à jour." },
  "errors": { "save_failed": "Échec de la mise à jour du compte." }
}
```

et `"users": "Comptes"` dans `admin.nav`. Traduire dans les quatre autres catalogues.

- [ ] **Step 8 : tests, build et garde de dérive**

Run: `npm test`
Expected: PASS

Run: `npm run build`
Expected: build réussi

Vérifier que le client généré n'a pas été modifié à la main :

Run: `npm run api:gen && git diff --ignore-cr-at-eol --stat -- src/app/api/`
Expected: aucune sortie (le client régénéré est identique à celui commité)

- [ ] **Step 9 : commit**

```bash
git add src/app/features/admin/users/ src/app/app.routes.ts src/app/core/layout/admin-layout/ public/i18n/
git commit -m "feat(admin): ecran de gestion de l'acces offert"
```

---

## Vérification finale du lot

Après merge des quatre PR, dans l'ordre Phase 1 → 2 → 3 → 4 :

- [ ] `pytest -q` vert dans `Poker_server` et `trainingmanager_server`
- [ ] `npm test && npm run build` vert dans les deux frontends
- [ ] CI verte sur les quatre repos — vérifier `gh pr checks` en relecture directe, pas via `--watch` (qui sort 0 même en échec)
- [ ] Le garde de dérive OpenAPI de `trainingmanager_frontend` est vert
- [ ] Scénario manuel de bout en bout : créer un compte de test, constater le blocage à la création d'équipe, activer l'accès offert depuis `/admin/users` (TM) et `/staff/users` (Poker), constater que la création passe et que le badge s'affiche

## Hors périmètre (suites possibles)

- Une page Profil sur Poker, qui n'existe pas aujourd'hui — le badge y déménagerait naturellement.
- Le lot B (tables persistantes), qui dépend du champ créé en Task 1 pour son `room_quota()`.
- Le portage de Stripe sur TrainingManager, préparé par `customuser/entitlements.py` mais non entamé.
