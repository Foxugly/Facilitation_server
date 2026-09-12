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

    #: Les cles attendues dans `Round.config`, et leur type — meme role que
    #: `payload_schema`, mais pour la configuration du round plutot que pour
    #: chaque reponse. Vide par defaut : le poker n'a aucune option propre
    #: aujourd'hui, donc toute cle qu'un client tenterait d'y poser est une
    #: erreur cote client, pas une donnee a stocker (tache 2, design 5c).
    config_schema: dict = field(default_factory=dict)

    #: Responses d'un item -> decompte. Vit ici et non dans `services` pour que
    #: l'objectif tienne : ajouter une activite ne doit toucher que ce fichier.
    #: Signature : (responses, card_values) -> {"tally": [...], "spread": {...}}.
    #: Laisse a None dans la declaration : `__post_init__` la lie au drapeau
    #: `ordinal` de cette meme specification, pour que le comptage par defaut
    #: (celui du poker aujourd'hui) reste inchange sans que chaque entree du
    #: registre ait a le repeter.
    aggregate: Callable[[list, list[str]], dict] = field(default=None)

    #: Une reponse deja conforme au `payload_schema` porte-t-elle une valeur
    #: JOUABLE ? Signature : (payload, card_values, item_count=0) -> bool. Vit
    #: dans le registre pour la meme raison que `aggregate` : une activite au
    #: payload different de `{"card": ...}` (ex. `{"points": 3}`) ne doit pas
    #: heriter d'une regle « la carte appartient au deck » qui ne la concerne
    #: pas. Laisse a None : `__post_init__` branche le defaut poker
    #: (appartenance au deck actif) si l'entree du registre n'en fournit pas.
    #:
    #: `item_count` (tache 6a-2, dot voting) : la borne d'une valeur peut
    #: dependre du nombre d'items du round (0 <= points <= n), une donnee que
    #: le registre ne possede pas — elle vit dans `Round`/`Item`, cote
    #: domaine. Deux formes etaient possibles : que la validation recoive ce
    #: dont elle a besoin en parametre, ou que le registre expose une
    #: fonction que le domaine appelle avec un objet de contexte. Choix
    #: retenu : la PREMIERE — un parametre positionnel de plus, exactement
    #: comme `card_values` deja aujourd'hui (une donnee que le registre ne
    #: possede pas plus, deja passee de cette facon). C'est aussi la forme de
    #: `aggregate` et `rank_value` juste a cote : des primitives passees
    #: directement, jamais un objet de contexte generique. Garder cette
    #: symetrie plutot qu'inventer un second canal pour cette seule donnee.
    #: Toute activite future qui aura besoin d'une autre donnee de contexte
    #: l'ajoutera de la meme facon, en parametre supplementaire.
    #:
    #: Defaut PRUDENT (`item_count=0`, meme esprit que `consumes`/`produces`
    #: plus bas) : le seul appel existant (`cast_response`,
    #: `realtime/services.py`) ne fournit encore que 2 arguments positionnels
    #: — le brancher sur `item_count` est l'ouverture de domaine que la
    #: tache SUIVANTE (validation a l'echelle du round, design section 3)
    #: fera. Tant qu'elle n'est pas faite, une strategie qui a besoin de
    #: `item_count` sans le recevoir doit se comporter de facon SURE plutot
    #: que de laisser passer une valeur arbitraire : ici, une borne haute a 0
    #: plutot qu'une borne ignoree.
    validate_value: Callable[[dict, list[str], int], bool] = field(default=None)

    #: La troisieme portee de validation (design section 3, point 3 ; brief
    #: tache 6a-3) : `validate_value` juge UNE reponse, celle-ci juge
    #: l'ENSEMBLE des reponses qu'UN participant a deja posees sur LE ROUND,
    #: plus la tentative en cours -- la seule echelle ou "la somme des jetons
    #: d'un participant <= 2n" ou "chaque poids utilise au plus une fois" (la
    #: future contrainte de weighted_dot_voting, tache 6b) ont un sens.
    #: Cette portee n'existait pas avant cette tache : `cast_response`
    #: validait une reponse a la fois.
    #:
    #: Signature : (existing, item_id, payload, item_count=0) -> bool.
    #: `existing` est la liste `[(item_id, payload), ...]` des reponses QUE CE
    #: PARTICIPANT A DEJA ECRITES sur ce round -- AVANT l'ecriture tentee,
    #: item_id/payload compris si une reponse existe deja pour cet item.
    #: `item_id`/`payload` portent la tentative en cours.
    #:
    #: PIEGE (brief tache 6a-3, c'est lui qui fait cette tache) : une reponse
    #: REMPLACE la precedente sur le meme item (`cast_response` ecrit via
    #: `update_or_create(item=..., participant=...)`) -- le budget se juge
    #: donc sur l'etat APRES remplacement, jamais en additionnant l'ancienne
    #: valeur ET la nouvelle. Une implementation doit donc ECRASER, dans
    #: `existing`, l'entree dont l'item_id correspond a celui de la tentative,
    #: avant de sommer/verifier quoi que ce soit -- jamais sommer `existing`
    #: tel quel puis ajouter `payload` a cote, ce qui compterait l'ancienne
    #: valeur deux fois.
    #:
    #: Defaut PRUDENT mais NON CONTRAIGNANT : `None` (le defaut du champ) est
    #: remplace par `_default_validate_responses` dans `__post_init__`, qui
    #: accepte TOUJOURS. Le poker n'a aucune regle de ce genre (design §8 :
    #: « pas de changement du poker ») -- une activite qui ne fournit pas ce
    #: hook ne doit RIEN voir changer, contrairement a `validate_value` dont
    #: le defaut REFUSE (appartenance au deck). Les deux defauts sont prudents
    #: chacun dans son sens : refuser par defaut une regle qu'on ignore
    #: (valeur par item), ne RIEN imposer par defaut a une echelle que
    #: personne avant dot_voting_v1 n'utilisait (ensemble du round).
    validate_responses: Callable[[list, int, dict, int], bool] = field(default=None)

    #: Qui a le droit de CREER un item sur un round de cette activite :
    #: "facilitator" (le facilitateur seul, comportement du poker) ou
    #: "participants" (tout participant -- un brainstorming, ou chacun ecrit
    #: son propre post-it). Defaut prudent : une activite dont la politique
    #: n'est pas connue ne doit pas ouvrir l'ecriture a tous (design §6).
    #: C'est ce champ, lu par `services.add_item`, qui autorise `item.add` a
    #: un participant ordinaire -- sans lui, ajouter le brainstorming aurait
    #: du toucher `services.py`, ce que le registre existe pour eviter.
    #: Quand elle vaut "participants", `services.update_item`/`remove_item`
    #: restreignent en plus un participant ordinaire a SES PROPRES items
    #: (`Item.author`) ; le facilitateur, lui, peut toujours agir sur n'importe
    #: lequel (design §5).
    items_authored_by: str = "facilitator"

    #: Ce que cette activite CONSOMME comme items : "items" (saisis a la main
    #: ou copies d'une source chainee) ou "none" si elle n'en a besoin d'aucun.
    #: Vocabulaire aligne sur le registre FRONT (`activity-registry.ts`,
    #: `consumes`/`produces`) -- deux vocabulaires pour la meme notion
    #: finiraient par diverger. Defaut prudent "none" : une strategie inconnue
    #: ne doit pas se declarer consommatrice sans qu'on sache de quoi
    #: (design 2026-09-11 §7).
    consumes: str = "none"

    #: Ce que cette activite PRODUIT pour une activite chainee en aval :
    #: "results" (un Result fige par item -- le poker) ou "none". Produire des
    #: "results" rend l'activite chainable : la tache suivante copiera ces
    #: Result en Item du round consommateur (design §7 -- la copie
    #: atterrit toujours en Item, quelle que soit la source). Meme defaut
    #: prudent que `consumes`.
    produces: str = "none"

    #: Un `Result` de cette activite porte-t-il un ORDRE exploitable pour un
    #: chainage "top N" (design 2026-09-11 §7) ? Signature :
    #: (Result) -> une cle triable, PLUS GRANDE = PLUS prioritaire. `None` (le
    #: defaut) veut dire "aucun classement" -- `bind_round` (services.py) doit
    #: alors REFUSER un `source_rule.top` non nul A LA DECLARATION, pas
    #: silencieusement l'ignorer au demarrage du round consommateur. Aucune
    #: activite du registre actuel (`delegation_v1`, `fist_of_five_v1`) n'en
    #: fournit : un niveau de delegation ou un score fist-of-five est un
    #: CONSENSUS par item, pas un ordre ENTRE items -- ce sera le rapport d'une
    #: future activite de type Dot Voting / Weighted Ranking, pas du code ici.
    rank_value: Callable[[object], object] | None = field(default=None)

    def __post_init__(self):
        if self.aggregate is None:
            object.__setattr__(self, "aggregate", _default_aggregate(self.ordinal))
        if self.validate_value is None:
            object.__setattr__(self, "validate_value", _default_validate_value)
        if self.validate_responses is None:
            object.__setattr__(self, "validate_responses", _default_validate_responses)


def _default_validate_value(payload, card_values, item_count=0):
    """La regle du poker : la carte jouee doit appartenir au deck actif.

    `item_count` est accepte et ignore : le poker n'en a jamais besoin, le
    parametre n'existe que pour les strategies (dot_voting_v1 et ce qui
    suivra) qui bornent leur valeur par le nombre d'items du round plutot
    que par un deck."""
    return payload.get("card") in card_values


def _default_validate_responses(existing, item_id, payload, item_count=0):
    """Aucune contrainte a l'echelle du round par defaut : le poker (et toute
    activite qui ne fournit pas ce hook) n'a rien de ce genre a verifier
    (design §8). Les arguments sont acceptes et ignores -- signature commune
    a tout `validate_responses` du registre."""
    return True


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


def _dot_voting_validate_value(payload, card_values, item_count=0):
    """Remplace la regle par defaut (« la valeur appartient au deck »),
    inapplicable ici : dot_voting_v1 n'a pas de cartes, `card_values` est
    donc toujours vide (design §7 -- un deck sans carte fige un snapshot a
    `cards: []`) et la regle par defaut refuserait TOUT. Preuve, comme
    l'annonce le design, que l'architecture du registre (5c) tenait : il
    suffit de fournir un autre `validate_value`, rien d'autre ne bouge.

    La borne vient du ROUND, pas du deck : chaque participant recoit 2n
    jetons de poids 1 (n = nombre d'items du round) et peut en poser au plus
    n sur un meme item -- d'ou 0 <= points <= n (design section 2-3, brief
    tache 6a-2). La contrainte GLOBALE (la somme d'un participant sur tout
    le round <= 2n) n'est PAS verifiee ici : elle porte sur l'ensemble des
    reponses d'un participant, pas sur une reponse a la fois, et c'est
    precisement l'ouverture de domaine que la tache suivante ajoute (design
    section 3, point 3).
    """
    points = payload.get("points")
    if isinstance(points, bool) or not isinstance(points, int):
        return False
    return 0 <= points <= item_count


def _dot_voting_validate_responses(existing, item_id, payload, item_count=0):
    """La contrainte GLOBALE que `_dot_voting_validate_value` annoncait ne
    PAS verifier (design section 3, point 3 ; tache 6a-3) : la somme des
    jetons qu'UN participant pose sur TOUT le round ne doit pas depasser son
    budget de 2n jetons (n = item_count, design section 1).

    PIEGE (brief 6a-3) : `existing` porte ce que ce participant a DEJA
    ecrit sur ce round, item_id compris si une reponse y existe deja --
    `cast_response` REMPLACE (`update_or_create`), donc l'entree de
    `existing` dont l'item_id correspond a la tentative en cours doit etre
    ECRASEE par `payload`, jamais additionnee a cote de lui. D'ou le dict
    (une seule valeur par item_id, la derniere ecrite gagne) plutot qu'une
    simple somme de la liste : `sum(p for _, p in existing) +
    payload.get("points", 0)` compterait deux fois l'ancienne valeur d'une
    correction sur le MEME item -- exactement le defaut que le brief demande
    d'epingler par un test, puis de verifier par mutation.
    """
    by_item = {i: p.get("points", 0) for i, p in existing}
    by_item[item_id] = payload.get("points", 0)
    return sum(by_item.values()) <= 2 * item_count


def _dot_voting_aggregate(responses, card_values):
    """Somme des points de CET item, tous participants confondus (design
    section 1 et 6) -- la meme fonction que reutilisera weighted_dot_voting
    (tache 6b), les deux activites partageant le depouillement (design
    section 1 : « elles partagent l'agregation, le classement et le
    rendu »).

    Forme volontairement differente du defaut poker (`{"tally": [...],
    "spread": {...}}`) : il n'y a ici ni carte a denombrer ni ecart ordinal,
    seulement un total. `card_values` est accepte pour respecter la
    signature commune a tout `aggregate` du registre, mais reste inutilise
    -- toujours vide pour cette strategie (design §7).

    Ouverture NON faite par cette tache, a signaler : `revealed_payload`
    (`realtime/services.py`) suppose encore la forme poker (`counted["tally"]`,
    `counted["spread"]`) et lit `payload.get("card")` pour le detail
    nominatif -- la generaliser pour consommer cette forme differente est
    un travail de domaine qui reste a faire (voir rapport de tache).
    """
    total = sum(r.payload.get("points", 0) for r in responses)
    return {"totalPoints": total, "responseCount": len(responses)}


def _dot_voting_rank_value(result):
    """Classement (design section 6) : le total de l'item, PLUS GRAND = PLUS
    prioritaire -- donc le total lui-meme, aucune transformation.

    Lit `Result.chosen_value` (CharField) comme une chaine d'entier : c'est
    le SEUL champ que `Result` porte aujourd'hui. Le design l'a deja
    identifie comme une dette (section 6 : « Result ne porte qu'une valeur
    de carte. Il lui faut un payload, additif »). Cette tache ne leve pas
    cette dette -- « Aucune migration » est une contrainte explicite de la
    tache 6a-2 -- et ne cable pas non plus `act_result`
    (`realtime/services.py`) pour y ecrire un total : ce `rank_value` est
    donc EXERCABLE des aujourd'hui (tests directs, et par
    `_chaining_candidate_items` si un `Result.chosen_value` porte deja un
    total ecrit par un autre moyen), mais rien ne l'alimente encore en
    production. A signaler comme ouverture de domaine restante, pas a faire
    ici.

    Departage des ex aequo : NE se fait PAS dans cette fonction, une cle de
    tri seule ne peut pas etre "stable" par elle-meme. Il se fait par
    construction, en amont : `sorted(..., key=rank_value, reverse=True)`
    est un tri STABLE en Python (garanti par le langage), et l'appelant
    (`_chaining_candidate_items`, `realtime/services.py`) trie deja sa liste
    source par `(sequence, id)` avant de reclasser par `rank_value`. Deux
    items a egalite de points ressortent donc toujours dans l'ordre de
    sequence de leur round source, jamais dans un ordre arbitraire —
    verifie par test (une valeur d'ordre incoherente casserait l'historique
    du chainage, brief tache 6a-2)."""
    return int(result.chosen_value)


#: Strategie -> specification. Une strategie absente retombe sur le defaut, qui
#: est volontairement NON ordinal : mieux vaut ne pas afficher d'ecart que d'en
#: afficher un faux sur une echelle dont on ignore l'ordre.
ACTIVITY_REGISTRY: dict[str, ActivitySpec] = {
    # Le poker consomme des items saisis (sujets) et produit un Result fige par
    # item -- il est donc chainable en amont d'une activite qui consommerait
    # des "items" (design §7).
    "delegation_v1": ActivitySpec(ordinal=True, consumes="items", produces="results"),
    "fist_of_five_v1": ActivitySpec(ordinal=True, consumes="items", produces="results"),
    # Dot Voting (design 2026-09-12, tache 6a-2) : chaque participant recoit 2n
    # jetons de poids 1 (n = nombre d'items du round) et en pose au plus n sur
    # un meme item -- {"points": <entier>}, valide item par item par
    # `_dot_voting_validate_value` (0 <= points <= n) ET, depuis la tache
    # 6a-3, a l'echelle du round entier par `_dot_voting_validate_responses`
    # (somme <= 2n, remplacement compris -- design section 3, point 3).
    # `consumes="items"` :
    # l'activite distribue des jetons sur des items existants, saisis ou
    # copies d'une source chainee. `produces="results"` + `rank_value` :
    # chainable, et c'est elle qui allume le "top N" du chainage (design
    # section 6), reste inutilisable depuis 5e faute d'une activite sachant
    # classer. `config_schema` : {"liveTotals": bool} -- le facilitateur
    # choisit si les TOTAUX (jamais le lien participant -> jetons) sont
    # visibles pendant le vote (design section 5). Absente de `Round.config`
    # (valeur par defaut du modele : {}) tant que le facilitateur n'a rien
    # choisi -- a lire cote domaine avec `.get("liveTotals", False)`, secret
    # par defaut, jamais un oubli de configuration.
    "dot_voting_v1": ActivitySpec(
        payload_schema={"points": int},
        config_schema={"liveTotals": bool},
        validate_value=_dot_voting_validate_value,
        validate_responses=_dot_voting_validate_responses,
        aggregate=_dot_voting_aggregate,
        rank_value=_dot_voting_rank_value,
        consumes="items",
        produces="results",
    ),
}

DEFAULT_SPEC = ActivitySpec()


def spec_for(strategy: str | None) -> ActivitySpec:
    """La specification d'une strategie, ou le defaut prudent."""
    return ACTIVITY_REGISTRY.get(strategy or "", DEFAULT_SPEC)


def _validate_against_schema(schema, data, rejected_type):
    """Coeur commun a `validate_payload` et `validate_config` : `data` doit
    porter EXACTEMENT les cles de `schema`, avec le bon type — ni cle
    manquante, ni cle en trop, ni type errone. Factorise pour que la regle ne
    puisse pas diverger entre payload et config comme l'a deja fait la regle
    ordinale avant le registre (voir l'en-tete du module)."""
    if not isinstance(data, dict) or set(data.keys()) != set(schema.keys()):
        raise RoomError(
            "state.invalid_transition", "Does not match schema", rejected_type
        )
    for key, expected_type in schema.items():
        if not isinstance(data.get(key), expected_type):
            raise RoomError(
                "state.invalid_transition", "Does not match schema", rejected_type
            )


def validate_payload(strategy, payload):
    """Verifie que `payload` porte exactement les cles du `payload_schema` de la
    strategie, avec le bon type. Leve `RoomError` plutot que de laisser
    `cast_response` ecrire un payload que l'activite ne sait pas relire."""
    _validate_against_schema(spec_for(strategy).payload_schema, payload, "response.cast")


def validate_config(strategy, config):
    """Meme regle que `validate_payload`, appliquee a `Round.config` plutot
    qu'a `Response.payload` : seul le `rejected_type` differe. Leve `RoomError`
    plutot que de laisser `configure_round` ecrire une configuration que
    l'activite ne sait pas relire."""
    _validate_against_schema(spec_for(strategy).config_schema, config, "round.configure")
