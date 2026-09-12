"""Peuplement des decks livres avec l'application.

Tous les decks partagent la meme face de carte et la meme encre : les illustrations
par deck etaient un placeholder du premier jet (« upload the real illustrations in
the admin ») dont les fichiers n'ont jamais ete televerses, si bien que les cartes
s'affichaient sur la couleur de repli. Un fond commun vaut mieux qu'un fond absent,
et reste remplacable carte par carte depuis l'admin.
"""
from decks.models import Card, Deck, TextLayer, TextLayerKind, VoteType

# Face commune a tous les decks : un eclat pastel, quasi blanc en son centre.
SHARED_CARD_FRONT = "decks/cards/front_cartes_foxugly.png"
# Encre commune. Ce fond etant tres clair, tout dessin ou texte clair y serait
# invisible — d'ou un quasi-noir, qui reprend la bordure de la carte. A revoir
# ensemble si la face commune change pour une illustration sombre.
CARD_INK = "#111111"

LEVELS = [
    ("1", "tell", "Tell", "Dire", "Vertellen", "Dire", "Decir"),
    ("2", "sell", "Sell", "Vendre", "Verkopen", "Vendere", "Vender"),
    ("3", "consult", "Consult", "Consulter", "Raadplegen", "Consultare", "Consultar"),
    ("4", "agree", "Agree", "S'accorder", "Afspreken", "Concordare", "Acordar"),
    ("5", "advise", "Advise", "Conseiller", "Adviseren", "Consigliare", "Aconsejar"),
    ("6", "inquire", "Inquire", "S'enquérir", "Informeren", "Informarsi", "Indagar"),
    ("7", "delegate", "Delegate", "Déléguer", "Delegeren", "Delegare", "Delegar"),
]
LANG_ORDER = ("en", "fr", "nl", "it", "es")


def create_standard_deck():
    vt, _ = VoteType.objects.get_or_create(
        code="delegation_poker", defaults={"resolution_strategy": "delegation_v1"}
    )
    vt.set_current_language("en")
    vt.name = "Delegation Poker"
    vt.save()

    deck = Deck.objects.create(
        vote_type=vt, is_standard=True, card_back_image=_standard_card_back()
    )
    deck.set_current_language("en")
    deck.name = "Delegation Poker"
    deck.save()

    for value, slug, *names in LEVELS:
        card = Card.objects.create(
            deck=deck, value=value, slug=slug, order=int(value),
            background_image=SHARED_CARD_FRONT,
        )
        num = TextLayer.objects.create(
            card=card, order=1, pos_x=12, pos_y=12, font_size=9, font_weight=700,
            color=CARD_INK, content_kind=TextLayerKind.STATIC,
        )
        num.set_current_language("en")
        num.content = value
        num.save()

        name_layer = TextLayer.objects.create(
            card=card, order=2, pos_x=50, pos_y=82, font_size=7, font_weight=600,
            color=CARD_INK, content_kind=TextLayerKind.I18N,
        )
        for lang, text in zip(LANG_ORDER, names):
            name_layer.set_current_language(lang)
            name_layer.content = text
            name_layer.save()
    return deck


# Repli seulement, quand le catalogue des dos est vide : ce nom est le placeholder
# historique, dont le fichier n'a jamais ete televerse. Y retomber affiche une carte
# face cachee nue — d'ou ``_standard_card_back()``, qui puise d'abord au catalogue.
FALLBACK_CARD_BACK = "decks/backs/back.webp"


def _standard_card_back():
    """Le dos par defaut d'un nouveau deck : le premier dos maison du catalogue.

    Le resoudre au lieu de coder un nom en dur evite de reproduire le placeholder
    d'origine, dont le fichier n'a jamais ete televerse. Ce dos ne sert qu'aux
    salles sans compte : une equipe qui a choisi le sien passe avant (selection.py).
    """
    from .models import CardBack

    back = CardBack.objects.filter(is_standard=True, is_active=True).order_by("pk").first()
    return back.image.name if back and back.image else FALLBACK_CARD_BACK


ICON_DIR = "decks/icons"

# (valeur, slug, ordre, fichier du pictogramme)
FIST_OF_FIVE_CARDS = [
    ("0", "fist-0", 0, f"{ICON_DIR}/fist-0.png"),
    ("1", "fist-1", 1, f"{ICON_DIR}/fist-1.png"),
    ("2", "fist-2", 2, f"{ICON_DIR}/fist-2.png"),
    ("3", "fist-3", 3, f"{ICON_DIR}/fist-3.png"),
    ("4", "fist-4", 4, f"{ICON_DIR}/fist-4.png"),
    ("5", "fist-5", 5, f"{ICON_DIR}/fist-5.png"),
]
ROMAN_VOTE_CARDS = [
    ("+1", "thumb-up", 1, f"{ICON_DIR}/thumb-up.png"),
    # Le neutre est un poing ferme — ni pour, ni contre — et non un pouce a
    # l'horizontale, d'ou « neutral » plutot que « side ».
    ("0", "thumb-neutral", 2, f"{ICON_DIR}/thumb-neutral.png"),
    ("-1", "thumb-down", 3, f"{ICON_DIR}/thumb-down.png"),
]


def _create_icon_deck(vote_type_code, resolution_strategy, names, cards):
    """Un deck muet : chaque carte porte une unique couche ``icon`` plein cadre.

    ``names`` est un dict {code_langue: nom du deck}. Les cartes n'ont aucune couche
    ``i18n`` : la valeur brute est ce qui s'affiche dans les surfaces de resultat, et
    elle a ete choisie pour se lire dans les cinq langues.
    """
    vt, _ = VoteType.objects.get_or_create(
        code=vote_type_code, defaults={"resolution_strategy": resolution_strategy}
    )
    vt.set_current_language("en")
    vt.name = names["en"]
    vt.save()

    deck = Deck.objects.create(
        vote_type=vt, is_standard=True, free_tier=False, card_back_image=_standard_card_back()
    )
    for lang, name in names.items():
        deck.set_current_language(lang)
        deck.name = name
        deck.save()

    for value, slug, order, icon_image in cards:
        card = Card.objects.create(
            deck=deck, value=value, slug=slug, order=order,
            background_image=SHARED_CARD_FRONT,
        )
        # Aucun texte : une couche icon dessine son image. Le pictogramme est une
        # donnee comme les autres visuels — en ajouter un est un televersement,
        # jamais une livraison du frontend.
        TextLayer.objects.create(
            card=card, order=1, pos_x=50, pos_y=50, font_size=55,
            color=CARD_INK, content_kind=TextLayerKind.ICON, image=icon_image,
        )
    return deck


def create_fist_of_five_deck():
    """Gradation d'adhesion de 0 a 5, SANS regle de veto : le 0 signifie
    « je n'adhere pas du tout », pas « je bloque »."""
    return _create_icon_deck(
        "fist_of_five",
        "fist_of_five_v1",
        # Nom de methode, garde tel quel dans les cinq langues (modifiable en admin).
        {lang: "Fist of Five" for lang in LANG_ORDER},
        FIST_OF_FIVE_CARDS,
    )


def create_roman_vote_deck():
    """Pour / neutre / contre. Les valeurs +1, 0 et -1 se lisent dans les cinq langues,
    ce qui evite toute traduction : elles ressortent brutes dans les surfaces de resultat."""
    return _create_icon_deck(
        "roman_vote",
        "roman_v1",
        {
            "en": "Roman Vote",
            "fr": "Vote romain",
            "nl": "Romeinse stemming",
            "it": "Voto romano",
            "es": "Voto romano",
        },
        ROMAN_VOTE_CARDS,
    )


DOT_VOTING_CODE = "dot_voting"
DOT_VOTING_RESOLUTION_STRATEGY = "dot_voting_v1"

DOT_VOTING_NAMES = {
    "en": "Dot Voting",
    "fr": "Vote par gommettes",
    "nl": "Stickerstemming",
    "it": "Voto con adesivi",
    "es": "Votación con pegatinas",
}


def create_dot_voting_deck():
    """Deck de l'activite Dot Voting : porte le type de vote, AUCUNE carte active.

    Design doc §7 : tout le runtime tire voteType/resolutionStrategy/cards du
    snapshot de deck fige sur le round, jamais des tables du referentiel. Un
    deck sans carte produit donc un snapshot a cards: [] qui porte quand meme
    le bon type -- pas de carte a inventer, l'activite se joue avec des jetons
    geres par le registre (realtime/activities.py), hors perimetre de ce seed.

    free_tier=False comme les decks icones : reserve aux equipes payantes tant
    que ce n'est pas offert par defaut aux salles sans compte.

    is_active=True depuis la tache 7 de la livraison 6a. Ce deck a ete seme
    is_active=False a la tache 1 de la meme livraison, et deliberement : regle
    du projet, un client ne se voit jamais propose un geste que le serveur
    refusera -- tant qu'aucune entree de registre (realtime/activities.py) ne
    connaissait "dot_voting", toute reponse aurait ete refusee par la regle par
    defaut "la valeur appartient au deck", un deck sans carte n'en ayant aucune.
    Cette entree de registre existe maintenant (`dot_voting_v1`), la condition
    qui tenait le deck ferme est donc levee -- les deux etats successifs
    (inactif puis actif) suivent la meme regle a deux moments differents de la
    livraison, ce n'est pas un oubli corrige.

    Une base qui a deja tourne ce seed avec l'ancien defaut (is_active=False)
    ne converge pas d'elle-meme : `seed_dot_voting_deck` s'arrete des qu'une
    ligne existe, il ne la met jamais a jour. Voir la migration de donnees
    `decks/migrations/0015_reactivate_dot_voting_deck.py`, qui rallume un deck
    "dot_voting" deja seme et reste inactif.
    """
    vt, _ = VoteType.objects.get_or_create(
        code=DOT_VOTING_CODE, defaults={"resolution_strategy": DOT_VOTING_RESOLUTION_STRATEGY}
    )
    vt.set_current_language("en")
    vt.name = DOT_VOTING_NAMES["en"]
    vt.save()

    deck = Deck.objects.create(
        vote_type=vt, is_standard=True, free_tier=False, is_active=True,
        card_back_image=_standard_card_back(),
    )
    for lang, name in DOT_VOTING_NAMES.items():
        deck.set_current_language(lang)
        deck.name = name
        deck.save()
    return deck
