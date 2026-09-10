from django.contrib import admin

from .models import Invitation, Team, TeamMembership


class MembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created_at")
    search_fields = ("name", "owner__email")
    inlines = [MembershipInline]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "team", "role", "created_at", "expires_at", "accepted_at")
    list_filter = ("role",)
    search_fields = ("email", "team__name")
