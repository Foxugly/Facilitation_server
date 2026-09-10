"""Endpoints staff d'administration du flag subscription_bypass (spec lot A §A.4).

- Lecture et mutation réservées aux superusers : `is_staff` ouvre la zone
  d'administration, offrir un accès gratuit engage de l'argent.
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
    return User.objects.create_superuser(email="staff@example.com", password="pw12345678")


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw12345678", display_name="Mimi")


@pytest.mark.django_db
def test_staff_can_search_users(staff, member):
    r = _client(staff).get("/api/v1/staff/users/?q=member")
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1 and results[0]["email"] == "member@example.com"
    assert results[0]["subscription_bypass"] is False


@pytest.mark.django_db
def test_non_staff_cannot_search_users(member):
    r = _client(member).get("/api/v1/staff/users/?q=member")
    assert r.status_code == 403


@pytest.mark.django_db
def test_anonymous_cannot_search_users(member):
    r = APIClient().get("/api/v1/staff/users/?q=member")
    assert r.status_code == 401


@pytest.mark.django_db
def test_staff_grants_bypass_and_stamps_granted_at(staff, member):
    r = _client(staff).patch(
        f"/api/v1/staff/users/{member.pk}/",
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
    _client(staff).patch(f"/api/v1/staff/users/{member.pk}/", {"subscription_bypass": True}, format="json")
    member.refresh_from_db()
    granted = member.bypass_granted_at
    assert granted is not None
    _client(staff).patch(f"/api/v1/staff/users/{member.pk}/", {"subscription_bypass": False}, format="json")
    member.refresh_from_db()
    assert member.subscription_bypass is False and member.bypass_granted_at == granted


@pytest.mark.django_db
def test_non_staff_cannot_grant_bypass(member):
    other = User.objects.create_user(email="other@example.com", password="pw12345678")
    r = _client(member).patch(f"/api/v1/staff/users/{other.pk}/", {"subscription_bypass": True}, format="json")
    assert r.status_code == 403
    other.refresh_from_db()
    assert other.subscription_bypass is False


@pytest.mark.django_db
def test_grant_then_revoke_writes_two_log_rows(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/v1/staff/users/{member.pk}/",
        {"subscription_bypass": True, "bypass_note": "asso X"},
        format="json",
    )
    _client(staff).patch(
        f"/api/v1/staff/users/{member.pk}/", {"subscription_bypass": False}, format="json"
    )

    rows = list(BypassGrantLog.objects.order_by("created_at"))
    assert [r.granted for r in rows] == [True, False]
    assert rows[0].actor_id == staff.pk and rows[0].target_id == member.pk
    assert rows[0].actor_label == staff.email
    assert rows[0].note == "asso X"


@pytest.mark.django_db
def test_unchanged_flag_writes_no_log_row(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/v1/staff/users/{member.pk}/", {"bypass_note": "note seule"}, format="json"
    )
    assert not BypassGrantLog.objects.exists()


@pytest.mark.django_db
def test_log_survives_actor_deletion(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/v1/staff/users/{member.pk}/", {"subscription_bypass": True}, format="json"
    )
    staff.delete()
    row = BypassGrantLog.objects.get()
    assert row.actor_id is None and row.actor_label == "staff@example.com"


@pytest.mark.django_db
def test_log_survives_target_deletion(staff, member):
    from accounts.models import BypassGrantLog

    _client(staff).patch(
        f"/api/v1/staff/users/{member.pk}/", {"subscription_bypass": True}, format="json"
    )
    member.delete()
    row = BypassGrantLog.objects.get()
    assert row.target_id is None and row.target_label == "member@example.com"


@pytest.mark.django_db
def test_a_plain_staff_account_cannot_grant_free_access(db, member):
    """La garde Angular exige `is_superuser` depuis toujours ; le serveur se
    contentait de `is_staff`. Les deux bouts disaient deux choses differentes,
    et c'est le serveur -- la seule vraie frontiere -- qui etait le plus laxiste.
    """
    simple_staff = User.objects.create_user(
        email="lecture@example.com", password="pw12345678", is_staff=True
    )

    r = _client(simple_staff).patch(
        f"/api/v1/staff/users/{member.id}/", {"subscription_bypass": True}, format="json"
    )

    member.refresh_from_db()
    assert r.status_code == 403
    assert member.subscription_bypass is False
