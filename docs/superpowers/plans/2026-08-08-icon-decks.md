# Decks à pictogrammes — plan d'implémentation

> **Pour les agents :** SOUS-SKILL REQUIS — utiliser `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans` pour dérouler ce plan tâche par tâche.
> Les étapes utilisent la syntaxe à cases à cocher (`- [ ]`).

**Objectif :** ajouter deux types de vote — Fist of Five (6 cartes, `0`–`5`) et vote romain
(3 cartes, `+1`/`0`/`-1`) — dont les cartes sont muettes et portent un pictogramme plein cadre.

**Architecture :** le moteur de vote est déjà générique ; l'essentiel du travail est du
référentiel. Le seul manque technique est le rendu d'une icône, résolu par une troisième
valeur `icon` de `TextLayerKind` (qui réutilise les champs de positionnement existants) et
un composant Angular portant les 9 pictogrammes en SVG inline. En prime, on active
`resolution_strategy`, dormant depuis l'origine, pour ne calculer l'écart min/max que sur
les échelles ordinales.

**Pile technique :** Django 6 · DRF · Channels · django-parler · pytest (backend) ;
Angular 21 · Transloco · vitest + TestBed (frontend).

**Spec de référence :** `docs/superpowers/specs/2026-08-08-icon-decks-design.md`

## Contraintes globales

- **Deux dépôts.** Backend `D:\Projects\PycharmProjects\Poker_server`, frontend
  `D:\Projects\WebstormProjects\Poker_frontend`. Chaque tâche indique lequel.
- **Jamais de commit sur `main`.** Les deux dépôts auto-déploient en production au push sur
  leur branche par défaut. Travailler sur `feat/icon-decks` (elle **existe déjà** côté
  backend et porte la spec ; à créer côté frontend). **Ne rien pousser** sans accord explicite.
- **Aucun texte sur les cartes.** Les deux decks sont muets par conception : une seule couche
  `icon` par carte, jamais de couche `i18n`.
- **`free_tier=False` et `is_standard=True`** pour les deux decks.
- **Cinq langues** dans les catalogues frontend : `en`, `fr`, `nl`, `it`, `es`. Le test
  `src/app/i18n-parity.spec.ts` échoue si une clé manque dans l'un d'eux.
- **Fond de carte partagé :** `decks/cards/front_cartes_foxugly.png` pour les 9 cartes.
- **Dos de carte :** `decks/backs/back.webp` (celui du deck existant).
- **Ne pas modifier** `cardName()` (`room.component.ts:206`) ni `_level_name()`
  (`history/api_views.py:25`) : leur repli sur la valeur brute est le comportement voulu.
- **Commandes de test.** Backend : `.venv\Scripts\python -m pytest` depuis la racine du dépôt.
  Frontend : `npm test`.

---

## Structure des fichiers

**Backend — `Poker_server`**

| Fichier | Responsabilité |
|---|---|
| `decks/models.py` *(modifié)* | Ajoute `TextLayerKind.ICON`. |
| `decks/migrations/0011_textlayer_icon_kind.py` *(créé)* | Migration du nouveau choix. |
| `rooms/snapshot.py` *(modifié)* | `_layer_text` sérialise une couche `icon` en chaîne simple. |
| `decks/seed.py` *(modifié)* | `create_fist_of_five_deck()` et `create_roman_vote_deck()`. |
| `decks/management/commands/seed_icon_decks.py` *(créé)* | Commande de peuplement idempotente. |
| `realtime/services.py` *(modifié)* | Routage de `resolution_strategy` pour l'écart min/max. |
| `rooms/tests/test_icon_layer.py` *(créé)* | Sérialisation d'une couche `icon`. |
| `decks/tests/test_icon_decks.py` *(créé)* | Peuplement, idempotence, exclusion du gratuit. |
| `realtime/tests/test_spread_strategy.py` *(créé)* | Écart calculé ou non selon la stratégie. |

**Frontend — `Poker_frontend`**

| Fichier | Responsabilité |
|---|---|
| `src/app/core/realtime/protocol.ts` *(modifié)* | Élargit `TextLayer.kind` à `'icon'`. |
| `src/app/shared/ui/card-icon/card-icon-keys.ts` *(créé)* | Liste des clés + type `CardIconName`. |
| `src/app/shared/ui/card-icon/card-icon.component.ts` *(créé)* | Les 9 pictogrammes SVG. |
| `src/app/shared/ui/card-icon/card-icon.component.spec.ts` *(créé)* | Test de complétude du jeu. |
| `src/app/shared/ui/delegation-card/delegation-card.component.ts` *(modifié)* | Rend une couche `icon`. |
| `src/app/shared/ui/delegation-card/delegation-card.component.spec.ts` *(créé)* | Icône vs texte. |
| `public/i18n/{en,fr,nl,it,es}.json` *(modifiés)* | Noms des deux types de poker. |

---

## Tâche 1 — La couche `icon` (backend)

**Dépôt :** `Poker_server`, branche `feat/icon-decks`

**Fichiers :**
- Modifier : `decks/models.py:12-14`
- Créer : `decks/migrations/0011_textlayer_icon_kind.py`
- Modifier : `rooms/snapshot.py:21-30`
- Tester : `rooms/tests/test_icon_layer.py`

**Interfaces :**
- Produit : `TextLayerKind.ICON` (valeur `"icon"`), consommé par la tâche 2.
- Produit : une couche `icon` sort de `build_deck_snapshot()` sous la forme
  `{"kind": "icon", "text": "<clé>", ...}` où `text` est une **chaîne**, jamais un
  dictionnaire par langue. Consommé par les tâches 4 et 5 (frontend).

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `rooms/tests/test_icon_layer.py` :

```python
"""Une couche `icon` porte une cle d'icone, pas de la prose : elle doit sortir du
snapshot en chaine simple, comme une couche `static`, et surtout PAS en dictionnaire
par langue — une cle d'icone ne se traduit pas."""
import pytest

from decks.models import TextLayer, TextLayerKind
from rooms.snapshot import build_deck_snapshot


@pytest.mark.django_db
def test_icon_layer_serialises_as_plain_string(standard_deck):
    card = standard_deck.cards.order_by("order").first()
    layer = TextLayer.objects.create(
        card=card, order=9, pos_x=50, pos_y=50, font_size=55,
        content_kind=TextLayerKind.ICON,
    )
    layer.set_current_language("en")
    layer.content = "fist-3"
    layer.save()

    snapshot = build_deck_snapshot(standard_deck)
    layers = snapshot["cards"][0]["layers"]
    icon = next(l for l in layers if l["kind"] == "icon")

    assert icon["text"] == "fist-3"
    assert isinstance(icon["text"], str)


@pytest.mark.django_db
def test_i18n_layer_still_serialises_as_a_dict(standard_deck):
    """Garde-fou de non-regression : le comportement existant ne bouge pas."""
    snapshot = build_deck_snapshot(standard_deck)
    layers = snapshot["cards"][0]["layers"]
    i18n = next(l for l in layers if l["kind"] == "i18n")

    assert isinstance(i18n["text"], dict)
    assert i18n["text"]["fr"] == "Dire"
```

- [ ] **Étape 2 : lancer le test et vérifier qu'il échoue**

Lancer : `.venv\Scripts\python -m pytest rooms/tests/test_icon_layer.py -v`
Attendu : ÉCHEC sur `AttributeError: ICON` — le membre d'énumération n'existe pas.

- [ ] **Étape 3 : ajouter le choix au modèle**

Dans `decks/models.py`, remplacer la classe `TextLayerKind` par :

```python
class TextLayerKind(models.TextChoices):
    STATIC = "static", "Static (one value, all languages)"
    I18N = "i18n", "Translated (per-language)"
    ICON = "icon", "Icon key (resolved by the client's icon registry)"
```

- [ ] **Étape 4 : générer la migration**

Lancer : `.venv\Scripts\python manage.py makemigrations decks -n textlayer_icon_kind`
Attendu : création de `decks/migrations/0011_textlayer_icon_kind.py` (une `AlterField`
sur `textlayer.content_kind`). Vérifier que le fichier ne contient **que** cette opération.

- [ ] **Étape 5 : sérialiser la couche `icon` comme une couche statique**

Dans `rooms/snapshot.py`, remplacer `_layer_text` par :

```python
def _layer_text(layer):
    """Static/icon → single string ; i18n → {lang: text} across LANGUAGES.

    Une couche ``icon`` porte une cle d'icone resolue par le client : ce n'est pas
    de la prose, elle ne se traduit pas et sort donc en chaine simple.
    """
    if layer.content_kind in (TextLayerKind.STATIC, TextLayerKind.ICON):
        return layer.safe_translation_getter("content", any_language=True) or ""
    text = {}
    for code, _ in settings.LANGUAGES:
        value = layer.safe_translation_getter("content", language_code=code, any_language=False)
        if value:
            text[code] = value
    return text
```

- [ ] **Étape 6 : lancer les tests et vérifier qu'ils passent**

Lancer : `.venv\Scripts\python -m pytest rooms/ decks/ -v`
Attendu : SUCCÈS, y compris les tests existants de `rooms/` et `decks/`.

- [ ] **Étape 7 : commiter**

```bash
git add decks/models.py decks/migrations/0011_textlayer_icon_kind.py rooms/snapshot.py rooms/tests/test_icon_layer.py
git commit -m "feat(decks): ajouter une couche de type icon au referentiel

Une couche icon porte une cle d'icone resolue par le client. Elle reutilise
les champs de positionnement existants et sort du snapshot en chaine simple,
comme une couche static : une cle d'icone ne se traduit pas."
```

---

## Tâche 2 — Peuplement des deux decks (backend)

**Dépôt :** `Poker_server`, branche `feat/icon-decks`

**Fichiers :**
- Modifier : `decks/seed.py` (ajouts en fin de fichier)
- Créer : `decks/management/commands/seed_icon_decks.py`
- Tester : `decks/tests/test_icon_decks.py`

**Interfaces :**
- Consomme : `TextLayerKind.ICON` (tâche 1).
- Produit : `create_fist_of_five_deck() -> Deck` et `create_roman_vote_deck() -> Deck`,
  importables depuis `decks.seed`.
- Produit : les clés d'icône `fist-0`…`fist-5`, `thumb-up`, `thumb-side`, `thumb-down`,
  consommées par la tâche 4 (frontend).

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `decks/tests/test_icon_decks.py` :

```python
"""Les deux decks a pictogrammes : contenu, idempotence, et exclusion de l'offre gratuite."""
import pytest
from django.core.management import call_command

from decks.models import Deck, TextLayerKind
from decks.selection import available_decks
from decks.seed import create_fist_of_five_deck, create_roman_vote_deck


@pytest.mark.django_db
def test_fist_of_five_has_six_mute_cards():
    deck = create_fist_of_five_deck()
    cards = list(deck.cards.order_by("order"))

    assert [c.value for c in cards] == ["0", "1", "2", "3", "4", "5"]
    for card in cards:
        layers = list(card.layers.all())
        assert len(layers) == 1
        assert layers[0].content_kind == TextLayerKind.ICON
        assert card.background_image.name == "decks/cards/front_cartes_foxugly.png"

    keys = [c.layers.first().safe_translation_getter("content", any_language=True) for c in cards]
    assert keys == ["fist-0", "fist-1", "fist-2", "fist-3", "fist-4", "fist-5"]


@pytest.mark.django_db
def test_roman_vote_orders_from_favourable_to_against():
    deck = create_roman_vote_deck()
    cards = list(deck.cards.order_by("order"))

    assert [c.value for c in cards] == ["+1", "0", "-1"]
    keys = [c.layers.first().safe_translation_getter("content", any_language=True) for c in cards]
    assert keys == ["thumb-up", "thumb-side", "thumb-down"]


@pytest.mark.django_db
def test_both_decks_are_reserved_to_paid_teams():
    create_fist_of_five_deck()
    create_roman_vote_deck()

    # Une salle sans compte ne voit que l'offre gratuite : ni l'un ni l'autre.
    free_codes = {d.vote_type.code for d in available_decks(None)}
    assert "fist_of_five" not in free_codes
    assert "roman_vote" not in free_codes


@pytest.mark.django_db
def test_roman_values_survive_into_the_snapshot():
    """Le « + » de « +1 » ne doit etre ni perdu ni normalise : c'est la valeur canonique
    stockee dans Vote.card_value et Result.chosen_value. `act_result` la validera par
    simple appartenance a `_card_values(room)`, donc aucun code supplementaire n'est
    necessaire — mais une valeur mangee au peuplement rendrait la carte invotable."""
    from rooms.snapshot import build_deck_snapshot

    snapshot = build_deck_snapshot(create_roman_vote_deck())
    assert [c["value"] for c in snapshot["cards"]] == ["+1", "0", "-1"]


@pytest.mark.django_db
def test_command_is_idempotent_per_vote_type():
    call_command("seed_icon_decks")
    call_command("seed_icon_decks")

    assert Deck.objects.filter(vote_type__code="fist_of_five").count() == 1
    assert Deck.objects.filter(vote_type__code="roman_vote").count() == 1
```

- [ ] **Étape 2 : lancer le test et vérifier qu'il échoue**

Lancer : `.venv\Scripts\python -m pytest decks/tests/test_icon_decks.py -v`
Attendu : ÉCHEC sur `ImportError: cannot import name 'create_fist_of_five_deck'`.

- [ ] **Étape 3 : écrire les fonctions de peuplement**

Ajouter à la fin de `decks/seed.py` :

```python
SHARED_CARD_FRONT = "decks/cards/front_cartes_foxugly.png"
SHARED_CARD_BACK = "decks/backs/back.webp"

# (valeur, slug, ordre, cle d'icone)
FIST_OF_FIVE_CARDS = [
    ("0", "fist-0", 0, "fist-0"),
    ("1", "fist-1", 1, "fist-1"),
    ("2", "fist-2", 2, "fist-2"),
    ("3", "fist-3", 3, "fist-3"),
    ("4", "fist-4", 4, "fist-4"),
    ("5", "fist-5", 5, "fist-5"),
]
ROMAN_VOTE_CARDS = [
    ("+1", "thumb-up", 1, "thumb-up"),
    ("0", "thumb-side", 2, "thumb-side"),
    ("-1", "thumb-down", 3, "thumb-down"),
]


def _create_icon_deck(vote_type_code, resolution_strategy, names, cards):
    """Un deck muet : chaque carte porte une unique couche ``icon`` plein cadre.

    ``names`` est un dict {code_langue: nom du deck}. Les cartes n'ont aucune couche
    ``i18n`` : la valeur brute est ce qui s'affiche dans les surfaces de resultat,
    et elle a ete choisie pour se lire dans les cinq langues.
    """
    vt, _ = VoteType.objects.get_or_create(
        code=vote_type_code, defaults={"resolution_strategy": resolution_strategy}
    )
    vt.set_current_language("en")
    vt.name = names["en"]
    vt.save()

    deck = Deck.objects.create(
        vote_type=vt, is_standard=True, free_tier=False, card_back_image=SHARED_CARD_BACK
    )
    for lang, name in names.items():
        deck.set_current_language(lang)
        deck.name = name
        deck.save()

    for value, slug, order, icon_key in cards:
        card = Card.objects.create(
            deck=deck, value=value, slug=slug, order=order,
            background_image=SHARED_CARD_FRONT,
        )
        layer = TextLayer.objects.create(
            card=card, order=1, pos_x=50, pos_y=50, font_size=55,
            color="#ffffff", content_kind=TextLayerKind.ICON,
        )
        layer.set_current_language("en")
        layer.content = icon_key
        layer.save()
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
```

- [ ] **Étape 4 : écrire la commande de gestion**

Créer `decks/management/commands/seed_icon_decks.py` :

```python
from django.core.management.base import BaseCommand

from decks.models import Deck
from decks.seed import create_fist_of_five_deck, create_roman_vote_deck


class Command(BaseCommand):
    help = "Create the two icon decks (Fist of Five, Roman Vote)."

    # Idempotence type par type : une execution partielle est relancable sans
    # effet de bord, chaque deck etant ignore separement s'il existe deja.
    DECKS = [
        ("fist_of_five", create_fist_of_five_deck),
        ("roman_vote", create_roman_vote_deck),
    ]

    def handle(self, *args, **options):
        for code, factory in self.DECKS:
            if Deck.objects.filter(vote_type__code=code, is_standard=True).exists():
                self.stdout.write(self.style.WARNING(f"Deck {code} already exists — skipping."))
                continue
            deck = factory()
            self.stdout.write(
                self.style.SUCCESS(f"Created {code} deck {deck.pk} with {deck.cards.count()} cards.")
            )
        self.stdout.write("Both decks are free_tier=False: enable them on a paid team to play.")
```

- [ ] **Étape 5 : lancer les tests et vérifier qu'ils passent**

Lancer : `.venv\Scripts\python -m pytest decks/ -v`
Attendu : SUCCÈS.

- [ ] **Étape 6 : commiter**

```bash
git add decks/seed.py decks/management/commands/seed_icon_decks.py decks/tests/test_icon_decks.py
git commit -m "feat(decks): peupler les decks Fist of Five et vote romain

Deux decks muets reserves aux equipes payantes : une seule couche icon
plein cadre par carte, aucun texte, un fond partage. La commande est
idempotente type par type."
```

---

## Tâche 3 — Routage de `resolution_strategy` (backend)

**Dépôt :** `Poker_server`, branche `feat/icon-decks`

**Fichiers :**
- Modifier : `realtime/services.py:38-45` (ajout d'un helper) et `:426-427`
- Tester : `realtime/tests/test_spread_strategy.py`

**Interfaces :**
- Consomme : les stratégies `fist_of_five_v1` et `roman_v1` posées par la tâche 2.
- Produit : `revealed_payload()` renvoie `spread = {"min": None, "max": None}` pour
  toute stratégie non ordinale. Le frontend le masque déjà (`room.component.html:82`).

**Pourquoi.** Sans ce routage, un vote romain calculerait son écart sur les seules valeurs
passant `isdigit()` : `+1` et `-1` échouent, `0` réussit — l'écran afficherait « 0 – 0 »,
un faux consensus, sous un vote pourtant partagé.

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `realtime/tests/test_spread_strategy.py` :

```python
"""L'ecart min/max n'a de sens que sur une echelle ordinale.

`delegation_v1` et `fist_of_five_v1` en ont une ; `roman_v1` non — ses valeurs
(+1/0/-1) sont des positions, pas des degres, et seul « 0 » passerait isdigit(),
ce qui afficherait un faux consensus.
"""
import pytest

from realtime.services import _spread_for


def test_ordinal_strategies_get_a_spread():
    assert _spread_for("delegation_v1", ["1", "5", "3"]) == {"min": 1, "max": 5}
    assert _spread_for("fist_of_five_v1", ["0", "4"]) == {"min": 0, "max": 4}


def test_roman_vote_gets_no_spread():
    assert _spread_for("roman_v1", ["+1", "0", "-1"]) == {"min": None, "max": None}


def test_unknown_strategy_gets_no_spread():
    """Repli prudent : une strategie inconnue n'invente pas d'echelle."""
    assert _spread_for("something_new_v1", ["1", "2"]) == {"min": None, "max": None}


def test_ordinal_strategy_without_numeric_votes_gets_no_spread():
    assert _spread_for("delegation_v1", []) == {"min": None, "max": None}
```

- [ ] **Étape 2 : lancer le test et vérifier qu'il échoue**

Lancer : `.venv\Scripts\python -m pytest realtime/tests/test_spread_strategy.py -v`
Attendu : ÉCHEC sur `ImportError: cannot import name '_spread_for'`.

- [ ] **Étape 3 : écrire le helper et la table de routage**

Dans `realtime/services.py`, ajouter juste après `_card_values` (vers la ligne 46) :

```python
# Principe P1 de la spec de modele de donnees : la DB decrit un type de vote,
# le code decide du comportement. Seules ces strategies ont une echelle ordinale
# sur laquelle un ecart min/max veut dire quelque chose.
ORDINAL_RESOLUTION_STRATEGIES = frozenset({"delegation_v1", "fist_of_five_v1"})


def _resolution_strategy(room):
    """La strategie du deck actif — session en cours si elle porte un snapshot,
    sinon celui de la salle. Meme regle de priorite que ``_card_values``."""
    session = room.current_session
    snapshot = (session.deck_snapshot if session and session.deck_snapshot else room.deck_snapshot)
    return (snapshot or {}).get("resolutionStrategy", "")


def _spread_for(strategy, card_values):
    """Ecart min/max des votes, ou {None, None} si l'echelle n'est pas ordinale."""
    if strategy not in ORDINAL_RESOLUTION_STRATEGIES:
        return {"min": None, "max": None}
    numeric = [int(v) for v in card_values if v.isdigit()]
    if not numeric:
        return {"min": None, "max": None}
    return {"min": min(numeric), "max": max(numeric)}
```

- [ ] **Étape 4 : brancher le helper dans `revealed_payload`**

Dans `realtime/services.py`, remplacer les deux lignes 426-427 :

```python
    numeric = [int(v.card_value) for v in votes if v.card_value.isdigit()]
    spread = {"min": min(numeric), "max": max(numeric)} if numeric else {"min": None, "max": None}
```

par :

```python
    spread = _spread_for(_resolution_strategy(room), [v.card_value for v in votes])
```

- [ ] **Étape 5 : lancer toute la suite et vérifier qu'elle passe**

Lancer : `.venv\Scripts\python -m pytest -v`
Attendu : SUCCÈS. Les tests existants de `realtime/` couvrent le Delegation Poker, dont
la stratégie reste ordinale : leur écart doit être **inchangé**. Si l'un d'eux échoue, c'est
que `resolutionStrategy` n'est pas lu au bon endroit — vérifier que les snapshots de test
portent bien `delegation_v1`.

- [ ] **Étape 6 : commiter**

```bash
git add realtime/services.py realtime/tests/test_spread_strategy.py
git commit -m "feat(realtime): router resolution_strategy pour l'ecart min/max

Le champ existait depuis l'origine, voyageait dans le snapshot et n'etait
route nulle part. Seules les strategies ordinales calculent desormais un
ecart ; le vote romain n'en affiche plus, ses valeurs etant des positions
et non des degres."
```

---

## Tâche 4 — Le jeu de pictogrammes (frontend)

**Dépôt :** `Poker_frontend` — **créer la branche** `feat/icon-decks` depuis `main`.

**Fichiers :**
- Modifier : `src/app/core/realtime/protocol.ts:15-26`
- Créer : `src/app/shared/ui/card-icon/card-icon-keys.ts`
- Créer : `src/app/shared/ui/card-icon/card-icon.component.ts`
- Tester : `src/app/shared/ui/card-icon/card-icon.component.spec.ts`

**Interfaces :**
- Consomme : les clés d'icône posées par la tâche 2 (`fist-0`…`fist-5`, `thumb-up`,
  `thumb-side`, `thumb-down`) et le `kind: "icon"` du snapshot (tâche 1).
- Produit : `CARD_ICON_KEYS` (tableau en lecture seule), le type `CardIconName`, et le
  composant `CardIconComponent` de sélecteur `app-card-icon`, entrée requise `name`.
  Consommés par la tâche 5.

> **Ce que l'exécution a appris.** Les pictogrammes ont été refaits **deux fois**, et à
> chaque fois c'est le rendu dans un navigateur qui a tranché, jamais les tests. Les aplats
> géométriques du premier jet ne se lisaient pas : le `fist-0` faisait moufle posée à plat,
> les pouces faisaient « L ». Aucun test ne pouvait l'attraper — vérifier que des formes
> existent ne dit rien de ce qu'elles composent.
>
> **La leçon à retenir pour tout travail de pictogramme : prévoir une étape de rendu visuel
> comme vérification à part entière.** Une suite verte ne prouve pas qu'une icône est lisible.

**Note sur les pictogrammes.** Ils sont dessinés **au trait** : contour d'épaisseur uniforme,
bouts et angles arrondis, aucune surface pleine. C'est ce qui garantit que `fist-3` et
`fist-4` se distinguent d'un coup d'œil, faiblesse connue d'un deck muet — vérifiée aussi à
petite taille. `fist-0` est un vrai poing (quatre phalanges repliées) et non une main mutilée :
ce deck est une gradation d'adhésion, il n'a pas de véto. Ces tracés vivent dans **un seul
fichier** ; les affiner plus tard ne touchera aucun câblage.

- [ ] **Étape 1 : créer la branche**

```bash
cd D:\Projects\WebstormProjects\Poker_frontend
git checkout main && git checkout -b feat/icon-decks
```

- [ ] **Étape 2 : écrire le test qui échoue**

Créer `src/app/shared/ui/card-icon/card-icon.component.spec.ts` :

```typescript
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { CARD_ICON_KEYS, CardIconName } from './card-icon-keys';
import { CardIconComponent } from './card-icon.component';

/**
 * Garde-fou de completude : toute cle servie par le backend doit avoir un trace.
 * Un oubli doit echouer en integration continue, pas en salle devant une equipe.
 */
@Component({
  standalone: true,
  imports: [CardIconComponent],
  template: `<app-card-icon [name]="name" />`,
})
class HostComponent {
  name: CardIconName = 'fist-0';
}

describe('CardIconComponent', () => {
  function render(name: CardIconName): HTMLElement {
    const fixture = TestBed.createComponent(HostComponent);
    fixture.componentInstance.name = name;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('covers exactly the nine keys seeded by the backend', () => {
    expect([...CARD_ICON_KEYS]).toEqual([
      'fist-0', 'fist-1', 'fist-2', 'fist-3', 'fist-4', 'fist-5',
      'thumb-up', 'thumb-side', 'thumb-down',
    ]);
  });

  for (const key of CARD_ICON_KEYS) {
    it(`draws a non-empty svg for "${key}"`, () => {
      const svg = render(key).querySelector('svg');
      expect(svg).not.toBeNull();
      // Le namespace SVG doit etre correct, sinon rien ne s'affiche a l'ecran.
      expect(svg!.namespaceURI).toBe('http://www.w3.org/2000/svg');
      expect(svg!.querySelectorAll('rect').length).toBeGreaterThan(0);
    });
  }

  it('raises one more finger from fist-3 to fist-4', () => {
    const three = render('fist-3').querySelectorAll('svg rect').length;
    const four = render('fist-4').querySelectorAll('svg rect').length;
    expect(four).toBe(three + 1);
  });
});
```

- [ ] **Étape 3 : lancer le test et vérifier qu'il échoue**

Lancer : `npm test -- card-icon`
Attendu : ÉCHEC — le module `./card-icon-keys` n'existe pas.

- [ ] **Étape 4 : écrire la liste des clés**

Créer `src/app/shared/ui/card-icon/card-icon-keys.ts` :

```typescript
/**
 * Les cles d'icone servies par le backend dans les couches de type `icon`
 * (voir `decks/seed.py`). L'ordre suit celui des cartes dans chaque deck.
 */
export const CARD_ICON_KEYS = [
  'fist-0',
  'fist-1',
  'fist-2',
  'fist-3',
  'fist-4',
  'fist-5',
  'thumb-up',
  'thumb-side',
  'thumb-down',
] as const;

export type CardIconName = (typeof CARD_ICON_KEYS)[number];

/** Une cle inconnue ne doit jamais tenter d'etre dessinee. */
export function isCardIconName(value: string): value is CardIconName {
  return (CARD_ICON_KEYS as readonly string[]).includes(value);
}
```

- [ ] **Étape 5 : écrire le composant**

Créer `src/app/shared/ui/card-icon/card-icon.component.ts`.

Chaque cas porte son **propre `<svg>` complet** : c'est ce qui garantit le namespace SVG,
qu'un `@switch` placé *à l'intérieur* d'un `<svg>` ne garantirait pas.

> **Les tracés définitifs vivent dans le composant, pas ici.** Ils ont changé deux fois à
> l'exécution — d'abord des aplats géométriques, jugés illisibles au rendu (le `fist-0`
> lisait comme une moufle, les pouces comme des « L »), puis un dessin **au trait**, seul
> retenu. Recopier des coordonnées dans ce plan ne ferait que créer une troisième version
> divergente : `card-icon.component.ts` fait foi.
>
> **État final : le jeu emploie deux techniques.** Les six mains du Fist of Five sont les
> **images fournies par Renaud** (`Poker_frontend/public/card-icons/fist-N.png`), affichées
> comme **masque CSS rempli en `currentColor`** — jamais comme `<img>`. Le dessin d'origine
> est noir sur fond transparent : posé tel quel il serait insensible au thème d'équipe, et
> invisible sur un fond sombre. Le masque règle les deux d'un coup. Les trois pouces du vote
> romain sont les tracés **PrimeIcons** (`thumbs-up`, `thumbs-down`), repris depuis
> `primeicons/raw-svg` — déjà une dépendance du projet, donc aucun paquet à ajouter, et sous
> licence MIT. Ils sont au trait comme les mains, là où les pouces en aplat dessinés
> auparavant détonnaient à côté d'elles. PrimeIcons n'ayant pas de pouce horizontal, le
> **neutre** est le pouce levé pivoté d'un quart de tour : même dessin, style homogène.
>
> Les deux jeux ne se croisent qu'au sélecteur de decks d'une équipe, une salle ne jouant
> qu'un deck à la fois — la différence de traitement ne se voit donc pas en partie.
>
> **Couleur des pictogrammes : `#111111`, pas blanc.** Le fond partagé est un éclat pastel
> quasi blanc en son centre ; un pictogramme clair y serait invisible. Un test backend
> verrouille la luminance de la couleur semée, faute de quoi le défaut ne se verrait qu'en
> salle.
>
> **Contrainte de licence à ne pas perdre de vue** : les tracés sont originaux. S'inspirer
> d'un style est libre, reprendre les tracés d'un jeu d'icônes sous licence (Icons8 et
> consorts) ne l'est pas — et ces deux decks constituent l'offre payante.


- [ ] **Étape 6 : élargir le type du protocole**

Dans `src/app/core/realtime/protocol.ts`, remplacer la ligne 16 :

```typescript
  kind: 'static' | 'i18n';
```

par :

```typescript
  /** `icon` porte une cle du registre de pictogrammes ; son `text` est toujours
   * une chaine, jamais un dictionnaire par langue (une cle ne se traduit pas). */
  kind: 'static' | 'i18n' | 'icon';
```

- [ ] **Étape 7 : lancer les tests et vérifier qu'ils passent**

Lancer : `npm test -- card-icon`
Attendu : SUCCÈS — 13 tests (2 de structure + 9 de tracé + 1 de namespace intégré + 1 de
progression des doigts).

- [ ] **Étape 8 : commiter**

```bash
git add src/app/shared/ui/card-icon src/app/core/realtime/protocol.ts
git commit -m "feat(cards): ajouter le jeu de neuf pictogrammes des decks muets

Six mains comptant de 0 a 5 doigts et trois pouces, en SVG inline statique.
Aucun jeu d'icones libre ne propose de main comptant jusqu'a cinq : ces
traces sont donc maison, volontairement schematiques pour que 3 et 4 doigts
se distinguent sans effort sur une carte sans texte."
```

---

## Tâche 5 — Rendu de la couche `icon` sur la carte (frontend)

**Dépôt :** `Poker_frontend`, branche `feat/icon-decks`

**Fichiers :**
- Modifier : `src/app/shared/ui/delegation-card/delegation-card.component.ts`
- Tester : `src/app/shared/ui/delegation-card/delegation-card.component.spec.ts`

**Interfaces :**
- Consomme : `CardIconComponent`, `CardIconName`, `isCardIconName` (tâche 4).
- Produit : le rendu final. Aucune tâche ultérieure n'en dépend.

- [ ] **Étape 1 : écrire le test qui échoue**

Créer `src/app/shared/ui/delegation-card/delegation-card.component.spec.ts` :

```typescript
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { SnapshotCard } from '../../../core/realtime/protocol';
import { DelegationCardComponent } from './delegation-card.component';

@Component({
  standalone: true,
  imports: [DelegationCardComponent],
  template: `<app-delegation-card [card]="card" [revealed]="true" lang="fr" />`,
})
class HostComponent {
  card!: SnapshotCard;
}

function cardWith(layers: SnapshotCard['layers']): SnapshotCard {
  return { value: '3', slug: 's', order: 1, background: { image: null }, layers };
}

const BASE = { order: 1, x: 50, y: 50, font: 'Inter', size: 55, weight: 400, color: '#ffffff', align: 'center' as const };

describe('DelegationCardComponent', () => {
  function render(card: SnapshotCard): HTMLElement {
    const fixture = TestBed.createComponent(HostComponent);
    fixture.componentInstance.card = card;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('draws an svg for an icon layer, and no text span', () => {
    const el = render(cardWith([{ ...BASE, kind: 'icon', text: 'fist-3' }]));
    expect(el.querySelector('app-card-icon svg')).not.toBeNull();
    expect(el.querySelector('span.layer')).toBeNull();
  });

  it('still draws a span for a text layer', () => {
    const el = render(cardWith([{ ...BASE, kind: 'i18n', text: { fr: 'Consulter', en: 'Consult' } }]));
    expect(el.querySelector('span.layer')?.textContent?.trim()).toBe('Consulter');
    expect(el.querySelector('app-card-icon')).toBeNull();
  });

  it('ignores an unknown icon key rather than drawing a broken card', () => {
    const el = render(cardWith([{ ...BASE, kind: 'icon', text: 'not-a-real-icon' }]));
    expect(el.querySelector('app-card-icon')).toBeNull();
  });
});
```

- [ ] **Étape 2 : lancer le test et vérifier qu'il échoue**

Lancer : `npm test -- delegation-card`
Attendu : ÉCHEC — aucun `app-card-icon` n'est rendu, et le premier test échoue.

- [ ] **Étape 3 : modifier le composant**

Dans `delegation-card.component.ts` :

Remplacer les imports et l'interface locale par :

```typescript
import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { FALLBACK_LANG } from '../../../core/i18n/available-languages';
import { SnapshotCard, TextLayer } from '../../../core/realtime/protocol';
import { CardIconName, isCardIconName } from '../card-icon/card-icon-keys';
import { CardIconComponent } from '../card-icon/card-icon.component';

interface PositionedLayer {
  style: Record<string, string>;
  text: string;
  /** Non nul uniquement pour une couche `icon` dont la cle est connue du registre. */
  icon: CardIconName | null;
}
```

Ajouter `imports: [CardIconComponent],` au décorateur, et remplacer le bloc `@if (faceUp())`
du template par :

```html
      @if (faceUp()) {
        @for (layer of layers(); track layer.style['top'] + layer.text) {
          @if (layer.icon) {
            <app-card-icon class="layer" [style]="layer.style" [name]="layer.icon" />
          } @else if (layer.text) {
            <span class="layer" [style]="layer.style">{{ layer.text }}</span>
          }
        }
      }
```

Remplacer `layers` et `styleFor` par :

```typescript
  readonly layers = computed<PositionedLayer[]>(() =>
    [...this.card().layers]
      .sort((a, b) => a.order - b.order)
      .map((layer) => {
        const icon = this.resolveIcon(layer);
        return {
          icon,
          text: icon ? '' : this.resolveText(layer),
          style: this.styleFor(layer, icon !== null),
        };
      }),
  );

  /** Une couche `icon` porte une cle du registre. Une cle inconnue — deck plus recent
   * que le client deploye — est ignoree : mieux vaut une carte nue qu'un trou visuel. */
  private resolveIcon(layer: TextLayer): CardIconName | null {
    if (layer.kind !== 'icon') return null;
    const key = typeof layer.text === 'string' ? layer.text : '';
    return isCardIconName(key) ? key : null;
  }

  private styleFor(layer: TextLayer, isIcon: boolean): Record<string, string> {
    const base: Record<string, string> = {
      left: `${layer.x}%`,
      top: `${layer.y}%`,
      color: layer.color,
    };
    if (isIcon) {
      // Une icone se dimensionne en boite, pas en corps de texte — mais toujours
      // en cqh, donc elle suit la taille de la carte comme le fait le texte.
      return { ...base, width: `${layer.size}cqh`, height: `${layer.size}cqh` };
    }
    return {
      ...base,
      'font-size': `${layer.size}cqh`,
      'font-weight': String(layer.weight),
      'text-align': layer.align,
    };
  }
```

- [ ] **Étape 4 : lancer les tests et vérifier qu'ils passent**

Lancer : `npm test`
Attendu : SUCCÈS sur toute la suite, y compris les specs existantes de la salle — le rendu
des couches de texte est inchangé.

- [ ] **Étape 5 : commiter**

```bash
git add src/app/shared/ui/delegation-card
git commit -m "feat(cards): rendre les couches icon sur la carte

Une couche icon devient un pictogramme dimensionne en cqh, qui hérite de la
couleur de la couche via currentColor — la personnalisation par equipe
fonctionne donc sans traitement particulier. Une cle inconnue est ignoree
plutot que de laisser un trou visuel sur un client plus ancien que le deck."
```

---

## Tâche 6 — Noms des deux types de poker (frontend)

**Dépôt :** `Poker_frontend`, branche `feat/icon-decks`

**Fichiers :**
- Modifier : `public/i18n/en.json`, `fr.json`, `nl.json`, `it.json`, `es.json`

**Interfaces :**
- Consomme : les codes de type `fist_of_five` et `roman_vote` (tâche 2), lus par
  `room.component.ts:165` sous la forme `room.deck.type.${d.voteType}`.

- [ ] **Étape 1 : ajouter les clés dans les cinq catalogues**

Dans chaque fichier, sous `room.deck.type` (à côté de `delegation_poker` et
`poker_planning`), ajouter :

| Fichier | `fist_of_five` | `roman_vote` |
|---|---|---|
| `en.json` | `"Fist of Five"` | `"Roman Vote"` |
| `fr.json` | `"Fist of Five"` | `"Vote romain"` |
| `nl.json` | `"Fist of Five"` | `"Romeinse stemming"` |
| `it.json` | `"Fist of Five"` | `"Voto romano"` |
| `es.json` | `"Fist of Five"` | `"Voto romano"` |

« Fist of Five » est un nom de méthode et reste tel quel dans les cinq langues — même
choix que côté backend, où `Deck.name` est modifiable en admin sans toucher au code.

- [ ] **Étape 2 : lancer le test de parité**

Lancer : `npm test -- i18n-parity`
Attendu : SUCCÈS. Ce test échoue si une clé manque dans l'un des cinq catalogues — c'est
lui qui garantit qu'aucune langue n'a été oubliée.

- [ ] **Étape 3 : lancer toute la suite**

Lancer : `npm test`
Attendu : SUCCÈS.

- [ ] **Étape 4 : commiter**

```bash
git add public/i18n
git commit -m "i18n(room): nommer les types Fist of Five et vote romain

Fist of Five reste tel quel dans les cinq langues : c'est un nom de methode."
```

---

## Vérification finale

- [ ] **Backend :** `.venv\Scripts\python -m pytest` → toute la suite passe.
- [ ] **Frontend :** `npm test` puis `npm run build` → suite verte et build de production réussi.
- [ ] **Bout en bout, en local :** lancer `seed_icon_decks`, activer les deux decks sur une
  équipe payante, ouvrir une salle et vérifier de visu que les neuf cartes s'affichent, que
  `fist-3` et `fist-4` se distinguent, et qu'un vote romain révélé **n'affiche aucun écart**
  sous le décompte.
- [ ] **Ne rien pousser** sans l'accord de Renaud : le push sur `main` déclenche
  l'auto-déploiement en production.

## Reste à faire, hors de ce plan

- Verser l'image `front_cartes_foxugly.png` dans le stockage média de production si elle n'y
  est pas déjà (elle est référencée en dur par le peuplement).
- Exécuter `seed_icon_decks` sur la production après déploiement.
- Décider si les deux decks doivent être annoncés sur les pages tarifs et fonctionnalités.
