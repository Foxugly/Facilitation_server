"""Delegation Board (design P2.5). A team's persistent board: rows = decision areas,
each carrying a current (AS-IS) and a target (TO-BE) delegation level. The board is
edited directly (post-its placed on a level), one per team."""
from django.db import models


class Board(models.Model):
    team = models.OneToOneField("teams.Team", on_delete=models.CASCADE, related_name="board")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Board<{self.pk}> team={self.team_id}"


class BoardRow(models.Model):
    """One decision area. as_is_level / to_be_level hold a card value ("1".."7") or
    null when the post-it hasn't been placed yet."""

    board = models.ForeignKey(Board, on_delete=models.CASCADE, related_name="rows")
    topic = models.CharField(max_length=200)
    as_is_level = models.CharField(max_length=32, null=True, blank=True)
    to_be_level = models.CharField(max_length=32, null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"BoardRow<{self.pk}> {self.topic}"
