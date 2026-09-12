"""Registre des activites, cote serveur.

Pendant du registre front (`activities/activity-registry.ts`). Meme objectif :
**ajouter une activite ne doit toucher que ce fichier**.

Le principe P1 de la spec de modele de donnees est deja celui-ci — « la DB decrit
un type de vote, le code decide du comportement » — mais ce comportement etait
disperse : un `frozenset` en tete de `services.py` pour l'echelle ordinale, des
conditions ailleurs. Le registre le rassemble et le nomme.

Clef = `VoteType.resolution_strategy`, l'identifiant que la DB porte et que le
code route (P1). PAS `VoteType.code` : deux types de vote peuvent partager une
strategie de resolution — un deck Fibonacci et un deck en t-shirt sizes se
depouillent tous deux comme une echelle ordinale.

Le registre porte aussi, depuis la tache 3, le schema de payload et
l'agregateur d'une activite : ajouter une activite ne doit toucher QUE ce
fichier, jamais `realtime/services.py`.
"""
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field


class RoomError(Exception):
    """Definie ici (et non dans `services.py`) pour que `validate_payload`
    puisse la lever sans creer d'import circulaire : `services` importe deja
    ce module. `services.py` la reexpose (`from realtime.activities import
    RoomError`) pour que le code et les tests existants qui font
    `from realtime.services import RoomError` continuent de fonctionner."""

    def __init__(self, code, message="", rejected_type=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.rejected_type = rejected_type


@dataclass(frozen=True, eq=False)
class ActivitySpec:
    """Ce qu'une strategie de resolution dit du depouillement.

    ``eq=False`` : ``payload_schema`` est un dict, non hachable — laisser
    ``frozen=True`` generer ``__eq__``/``__hash__`` sur les champs ferait
    lever ``TypeError`` au premier ``hash(spec)``. Rien n'en depend
    aujourd'hui, mais rien n'empeche non plus qu'un futur registre range des
    `ActivitySpec` dans un ``set`` ou une cle de dict.
    """

    #: L'echelle est-elle ORDINALE, c'est-a-dire un ecart min/max a-t-il un sens ?
    #: Un vote romain (+1 / 0 / -1) ou un jeu de pictogrammes n'ont pas d'ordre :
    #: calculer leur ecart produirait un « 0 - 0 » de faux consensus a partir des
    #: seules valeurs qui passent isdigit(). D'ou ce drapeau plutot qu'une
    #: tentative de conversion.
    ordinal: bool = False

    #: Les etats du cycle que l'activite emprunte. Le poker tourne sur le cycle
    #: historique, sans CLOSED ; une activite qui ne revele rien (brainstorming,
    #: affinity mapping) s'arretera a CLOSED sans jamais passer REVEALED.
    states: tuple[str, ...] = ("idle", "open", "revealed", "acted")

    #: Options reservees aux equipes payantes que l'activite expose. Doit rester
    #: aligne avec `teamOptions` du registre front : c'est le meme contrat, vu des
    #: deux cotes.
    team_options: tuple[str, ...] = field(default=("timer", "anonymous", "deck"))

    #: Les cles attendues dans `Response.payload`, et leur type. Le domaine les
    #: valide AVANT d'ecrire : une activite qui ajoute une cle ne touche que ce
    #: fichier, jamais `services.cast_response`.
    payload_schema: dict = field(default_factory=lambda: {"card": str})

    #: Responses d'un item -> decompte. Vit ici et non dans `services` pour que
    #: l'objectif tienne : ajouter une activite ne doit toucher que ce fichier.
    #: Signature : (responses, card_values) -> {"tally": [...], "spread": {...}}.
    #: Laisse a None dans la declaration : `__post_init__` la lie au drapeau
    #: `ordinal` de cette meme specification, pour que le comptage par defaut
    #: (celui du poker aujourd'hui) reste inchange sans que chaque entree du
    #: registre ait a le repeter.
    aggregate: Callable[[list, list[str]], dict] = field(default=None)

    #: Une reponse deja conforme au `payload_schema` porte-t-elle une valeur
    #: JOUABLE ? Signature : (payload, card_values) -> bool. Vit dans le
    #: registre pour la meme raison que `aggregate` : une activite au payload
    #: different de `{"card": ...}` (ex. `{"dots": 3}`) ne doit pas heriter
    #: d'une regle « la carte appartient au deck » qui ne la concerne pas.
    #: Laisse a None : `__post_init__` branche le defaut poker (appartenance
    #: au deck actif) si l'entree du registre n'en fournit pas.
    validate_value: Callable[[dict, list[str]], bool] = field(default=None)

    def __post_init__(self):
        if self.aggregate is None:
            object.__setattr__(self, "aggregate", _default_aggregate(self.ordinal))
        if self.validate_value is None:
            object.__setattr__(self, "validate_value", _default_validate_value)


def _default_validate_value(payload, card_values):
    """La regle du poker : la carte jouee doit appartenir au deck actif."""
    return payload.get("card") in card_values


def _default_aggregate(ordinal):
    """Le comptage actuel du poker (`Counter` + ecart ordinal), inchange par la
    tache 3 : c'est lui que toute strategie sans agregateur explicite recoit."""

    def aggregate(responses, card_values):
        values = [r.payload.get("card") for r in responses]
        counts = Counter(values)
        tally = [
            {"cardValue": value, "count": counts[value]}
            for value in card_values
            if counts.get(value)
        ]
        spread = {"min": None, "max": None}
        if ordinal:
            numeric = [int(v) for v in values if isinstance(v, str) and v.isdigit()]
            if numeric:
                spread = {"min": min(numeric), "max": max(numeric)}
        return {"tally": tally, "spread": spread}

    return aggregate


#: Strategie -> specification. Une strategie absente retombe sur le defaut, qui
#: est volontairement NON ordinal : mieux vaut ne pas afficher d'ecart que d'en
#: afficher un faux sur une echelle dont on ignore l'ordre.
ACTIVITY_REGISTRY: dict[str, ActivitySpec] = {
    "delegation_v1": ActivitySpec(ordinal=True),
    "fist_of_five_v1": ActivitySpec(ordinal=True),
}

DEFAULT_SPEC = ActivitySpec()


def spec_for(strategy: str | None) -> ActivitySpec:
    """La specification d'une strategie, ou le defaut prudent."""
    return ACTIVITY_REGISTRY.get(strategy or "", DEFAULT_SPEC)


def validate_payload(strategy, payload):
    """Verifie que `payload` porte exactement les cles du `payload_schema` de la
    strategie, avec le bon type — ni cle manquante, ni cle en trop, ni type
    errone. Leve `RoomError` plutot que de laisser `cast_response` ecrire un
    payload que l'activite ne sait pas relire."""
    schema = spec_for(strategy).payload_schema
    if not isinstance(payload, dict) or set(payload.keys()) != set(schema.keys()):
        raise RoomError(
            "state.invalid_transition", "Payload does not match schema", "response.cast"
        )
    for key, expected_type in schema.items():
        if not isinstance(payload.get(key), expected_type):
            raise RoomError(
                "state.invalid_transition", "Payload does not match schema", "response.cast"
            )
