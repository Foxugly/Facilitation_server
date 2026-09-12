"""Design doc §7 : une activite sans cartes doit se ranger dans le snapshot sans
tordre l'architecture. Le deck dot_voting ne cree aucune carte active -- le snapshot
doit quand meme porter le bon voteType/resolutionStrategy, avec cards: [].

Le deck est seme ACTIF depuis la tache 7 de la livraison 6a : le registre
d'activites (realtime/activities.py) connait desormais "dot_voting"
(`dot_voting_v1`), la condition qui le tenait ferme (« un client ne se voit
jamais proposer un geste que le serveur refusera ») est levee. Ces tests
epinglent que le deck apparait bien dans le catalogue d'une equipe, PAS dans le
catalogue gratuit (free_tier=False, decision produit), et qu'un rejeu du seed
ne revient jamais sur une desactivation deliberee.
"""
import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from decks.models import Deck, VoteType
from decks.seed import create_dot_voting_deck
from decks.selection import available_decks
from rooms.snapshot import build_deck_snapshot
from teams.models import Team, TeamMembership, TeamRole

User = get_user_model()


@pytest.mark.django_db
def test_snapshot_of_a_cardless_deck_carries_its_vote_type_and_no_cards():
    deck = create_dot_voting_deck()

    snapshot = build_deck_snapshot(deck)

    assert snapshot["voteType"] == "dot_voting"
    assert snapshot["resolutionStrategy"] == "dot_voting_v1"
    assert snapshot["cards"] == []


@pytest.mark.django_db
def test_seeded_deck_is_active_by_default():
    deck = create_dot_voting_deck()

    assert deck.is_active is True


@pytest.mark.django_db
def test_active_deck_appears_in_a_team_catalog():
    """decks.selection.available_decks est la source unique du catalogue -- si ce
    test passe alors que le deck resterait injouable, c'est bien ce filtre qui
    tient le catalogue, pas une supposition."""
    create_dot_voting_deck()
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
    team = Team.objects.create(name="Squad", owner=owner)
    TeamMembership.objects.create(team=team, user=owner, role=TeamRole.OWNER)

    codes = {d.vote_type.code for d in available_decks(team)}

    assert "dot_voting" in codes


@pytest.mark.django_db
def test_active_deck_is_still_excluded_from_the_free_catalog():
    """free_tier=False reste une decision produit (reserve aux equipes), pas un
    oubli -- distincte de is_active, que cette livraison bascule."""
    create_dot_voting_deck()

    codes = {d.vote_type.code for d in available_decks(None)}

    assert "dot_voting" not in codes


@pytest.mark.django_db
def test_command_is_idempotent():
    call_command("seed_dot_voting_deck")
    call_command("seed_dot_voting_deck")

    assert VoteType.objects.filter(code="dot_voting").count() == 1
    assert Deck.objects.filter(vote_type__code="dot_voting").count() == 1


@pytest.mark.django_db
def test_replaying_the_command_never_reverts_a_deliberate_deactivation():
    """Symetrique de l'ancien test (le deck naissait inactif ; il nait
    desormais actif) : un operateur qui DESACTIVE le deck ne doit pas le voir
    redevenir actif au prochain deploiement -- le seed skippe des qu'une ligne
    existe, il ne la met jamais a jour. La migration 0015 ne rouvre pas cette
    porte : Django ne rejoue jamais une migration deja appliquee, donc une
    desactivation posterieure a son passage lui survit. Seule une desactivation
    faite AVANT le deploiement qui applique 0015 serait ecrasee par elle --
    c'est l'objet meme de cette migration (faire converger les bases semees
    avec l'ancien defaut), pas un effet de bord."""
    call_command("seed_dot_voting_deck")
    deck = Deck.objects.get(vote_type__code="dot_voting")
    deck.is_active = False
    deck.save()

    call_command("seed_dot_voting_deck")

    deck.refresh_from_db()
    assert deck.is_active is False
    assert Deck.objects.filter(vote_type__code="dot_voting").count() == 1


@pytest.mark.django_db(transaction=True)
def test_migration_reactivates_an_existing_inactive_deck():
    """0015_reactivate_dot_voting_deck : une base qui a seme le deck AVANT ce
    commit (is_active=False, ancien defaut) doit converger vers le nouveau
    defaut au deploiement -- sinon elle resterait fermee pour toujours, le
    seed ne mettant jamais a jour une ligne existante (motif suivi de
    `rooms/tests/test_round_sequence.py::test_migration_backfills_sequence_by_creation_order_per_room`).
    """
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    def _migrate(targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    old = _migrate([("decks", "0014_background")])

    VoteType = old.get_model("decks", "VoteType")
    Deck = old.get_model("decks", "Deck")

    vt = VoteType.objects.create(code="dot_voting", resolution_strategy="dot_voting_v1")
    deck = Deck.objects.create(vote_type=vt, is_standard=True, free_tier=False, is_active=False)
    # Un second deck INACTIF, d'un AUTRE vote_type -- et inactif justement pour
    # que l'assertion ait du pouvoir de detection : un deck temoin DEJA actif
    # serait reste actif quoi que fasse la migration, y compris si elle rallumait
    # tout le catalogue. Inactif, il epingle reellement le filtre
    # `vote_type__code="dot_voting"` : le retirer par mutation fait echouer ce test.
    other_vt = VoteType.objects.create(code="roman_vote", resolution_strategy="roman_v1")
    other_deck = Deck.objects.create(vote_type=other_vt, is_standard=True, free_tier=False, is_active=False)

    try:
        new = _migrate([("decks", "0015_reactivate_dot_voting_deck")])
        DeckNew = new.get_model("decks", "Deck")

        assert DeckNew.objects.get(pk=deck.pk).is_active is True
        assert DeckNew.objects.get(pk=other_deck.pk).is_active is False
        assert DeckNew.objects.filter(vote_type__code="dot_voting").count() == 1
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
