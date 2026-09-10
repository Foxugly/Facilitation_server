"""Cache local des droits, alimenté par le service de facturation centralisé.

Ce modèle ne décide plus de rien : il **enregistre** ce que le central lui pousse.
Poker s'en sert pour son gating sans jamais appeler le réseau — si le central est
injoignable, les utilisateurs déjà payants continuent d'être servis.

Il est jetable : `sync_entitlements` côté central le reconstruit intégralement.
"""
from django.conf import settings
from django.db import models


class Subscription(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscription")
    stripe_customer_id = models.CharField(max_length=64, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=32, blank=True, default="")  # Stripe status; "" = never subscribed
    plan = models.CharField(max_length=16, blank=True, default="")  # "team1" | "team5"
    interval = models.CharField(max_length=8, blank=True, default="")  # "monthly" | "yearly"
    current_period_end = models.DateTimeField(null=True, blank=True)
    # Poussés par le central : ce n'est plus à Poker de décider qui est payant.
    # `is_paid` intègre déjà la période de grâce côté central — Poker n'a aucune
    # règle métier de facturation à réimplémenter.
    is_paid = models.BooleanField(default=False)
    quotas = models.JSONField(default=dict, blank=True)
    grace_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Subscription<{self.pk}> user={self.user_id} {self.plan}/{self.status}"


class DeliveryReceipt(models.Model):
    """Accusé d'une livraison déjà traitée, pour l'idempotence.

    Le central peut rejouer une livraison : reprise après échec réseau, rejeu
    manuel depuis la console, réconciliation quotidienne. Sans cette table, un
    rejeu tardif réappliquerait un état périmé par-dessus un état plus récent.
    """

    delivery_id = models.UUIDField(primary_key=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-received_at",)

    def __str__(self):
        return str(self.delivery_id)
