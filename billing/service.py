"""Gating local, alimenté par le cache de droits (§L4).

Poker ne calcule plus rien : il lit ce que le service central lui a poussé. Le
contrat de ce module n'a pas changé — `user_is_paid`, `user_quota`, `team_is_paid`
et `paid_required` sont appelés partout dans le code et gardent leur sémantique.

Ce qui a changé : « configuré » ne veut plus dire « une clé Stripe est présente »
mais « le service de facturation est branché ». Tant qu'il ne l'est pas, tout
reste ouvert — c'est ce qui permet de déployer cette migration sans rien casser.
"""
from django.conf import settings
from django.utils import timezone

# Quota "illimité" : valeur haute plutôt qu'un None, pour que les comparaisons
# numériques des appelants restent valides sans cas particulier.
UNLIMITED = 10_000


def billing_configured() -> bool:
    """Vrai quand Poker sait où joindre le central et avec quel secret."""
    return bool(settings.BILLING_BASE_URL and settings.BILLING_APP_SECRET)


def quota_for_plan(plan: str) -> int:
    """Repli local si le central n'a pas encore poussé de quotas pour ce plan."""
    return settings.PLAN_QUOTAS.get(plan, 0)


def _active_subscription(user):
    """L'abonnement local s'il ouvre encore des droits.

    `is_paid` vient du central et intègre déjà la période de grâce. On revérifie
    tout de même `current_period_end` : si le central se tait longtemps (panne,
    livraison perdue), le cache expire tout seul plutôt que d'ouvrir l'accès
    indéfiniment.
    """
    sub = getattr(user, "subscription", None)
    if sub is None or not sub.is_paid:
        return None
    if sub.current_period_end is not None and sub.current_period_end < timezone.now():
        if sub.grace_until is None or sub.grace_until < timezone.now():
            return None
    return sub


def user_is_paid(user) -> bool:
    """Si l'utilisateur peut utiliser les fonctions payantes (posséder des équipes).

    Un compte offert (`subscription_bypass`) passe toujours. Inerte (True) tant que
    la facturation n'est pas branchée.
    """
    if getattr(user, "subscription_bypass", False):
        return True
    if not billing_configured():
        return True
    return _active_subscription(user) is not None


def user_quota(user) -> int:
    """Nombre maximum d'équipes que l'utilisateur peut posséder.

    Les quotas viennent du central (`{"teams": 1}`) ; le dictionnaire local ne
    sert que de repli, pour ne pas dépendre d'un déploiement du central pour
    afficher un chiffre.
    """
    if getattr(user, "subscription_bypass", False):
        return UNLIMITED
    if not billing_configured():
        return UNLIMITED
    sub = _active_subscription(user)
    if sub is None:
        return 0
    quota = (sub.quotas or {}).get("teams")
    return quota if isinstance(quota, int) else quota_for_plan(sub.plan)


def team_is_paid(team) -> bool:
    return user_is_paid(team.owner)


def paid_required(team):
    """402 error_response quand le propriétaire n'a pas d'abonnement actif, sinon None.
    Inerte tant que la facturation n'est pas branchée."""
    if team_is_paid(team):
        return None
    from config.api_errors import error_response

    return error_response(
        code="subscription_required", detail="A subscription is required for this feature.", http_status=402
    )
