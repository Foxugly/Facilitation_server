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

Depuis la tache 6a-4, il porte en plus tout ce qui restait poker-specifique
dans le domaine : la FORME d'une reponse dans le depouillement nominatif
(`response_view`), le POINT ou le resultat se fige (`freeze_results`) et la
regle qui juge la valeur retenue a l'acte (`validate_chosen_value`). Les trois
defauts reproduisent a l'identique le code en dur qu'ils remplacent, pour que
le poker ne voie rien changer.

Depuis la tache 6a-5, il porte aussi `remaining_budget` : ce qu'il reste a
placer, PAR PARTICIPANT, reserve au facilitateur seul et filtre a l'emission
(`realtime/consumers.py`, jamais un masquage cote client). Defaut `None` (le
poker n'a aucune notion de budget) -- inchange pour lui.
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
    #:
    #: AVERTISSEMENT A L'AUTEUR D'UNE NOUVELLE ACTIVITE (round de correction 1,
    #: tache 6a-4) : **ce que rend cette fonction est DIFFUSE tel quel a toute
    #: la salle, round anonyme compris** -- `revealed_payload` le fusionne
    #: verbatim dans le bloc de l'item. N'y mettre JAMAIS une valeur
    #: individuelle ni rien qui permette de remonter a un participant : un
    #: agregat, et rien d'autre. L'anonymat n'est pas filtre ici, il tient
    #: parce que seul un agregat y transite. Le detail nominatif a son propre
    #: point d'accroche, `response_view`, que `revealed_payload` n'appelle que
    #: sur un round NON anonyme.
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
    #: tache SUIVANTE (validation a l'echelle du round, design §3)
    #: fera. Tant qu'elle n'est pas faite, une strategie qui a besoin de
    #: `item_count` sans le recevoir doit se comporter de facon SURE plutot
    #: que de laisser passer une valeur arbitraire : ici, une borne haute a 0
    #: plutot qu'une borne ignoree.
    validate_value: Callable[[dict, list[str], int], bool] = field(default=None)

    #: La troisieme portee de validation (design §3, point 3 ; brief
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

    #: Comment une reponse INDIVIDUELLE se donne a lire dans le depouillement
    #: NOMINATIF (`services.revealed_payload`, cle `votes`). Signature :
    #: (payload) -> dict, fusionne a cote de `participantId` dans chaque entree.
    #: Defaut : la forme historique du poker, `{"cardValue": payload["card"]}`.
    #:
    #: Vit dans le registre parce que `revealed_payload` lisait jusqu'ici
    #: `r.payload.get("card")` EN DUR : une activite dont le payload ne porte
    #: aucune carte (dot_voting_v1 : `{"points": n}`) y aurait diffuse un
    #: `cardValue: null` par participant.
    #:
    #: ATTENTION -- l'invariant de secret ne tient PAS dans cette fonction, il
    #: tient un cran au-dessus : `revealed_payload` ne l'APPELLE que sur un
    #: round non anonyme, et ne construit donc jamais les valeurs individuelles
    #: d'un round anonyme. Une activite ne doit jamais s'en servir pour
    #: « masquer » quoi que ce soit : le serveur ne construit pas ce qui doit
    #: rester secret, il ne le cache pas.
    response_view: Callable[[dict], dict] = field(default=None)

    #: OU se fige le resultat de cette activite -- et, par voie de consequence,
    #: ce que `result.act` a encore a ecrire.
    #:
    #: `None` (defaut, le poker) : rien ne se fige a la revelation. Le `Result`
    #: nait du GESTE du facilitateur (`result.act` -> `services.act_result`),
    #: qui retient une carte ; `revealed_payload` reagrege a chaque appel.
    #: Comportement historique, inchange.
    #:
    #: Non-`None` : l'activite fige son resultat A LA REVELATION, depuis les
    #: agregats -- et `act_result` ne fait plus alors que conclure le round,
    #: sans rien reecrire. Signature :
    #: (aggregates) -> {item_id: {"chosenValue": str, "payload": dict}}.
    #: `aggregates` est `[(item_id, counted), ...]` pour TOUS les items du
    #: round, dans l'ordre (sequence, id), `counted` etant ce que rend
    #: `aggregate` pour cet item. Un item absent du dict rendu ne recoit pas
    #: de `Result`.
    #:
    #: Pourquoi UN champ pour DEUX consequences (figer a la revelation, et ne
    #: plus ecrire a l'acte) : c'est une seule decision d'activite -- « ou se
    #: fige mon resultat » -- et la scinder en deux declarations permettrait de
    #: les rendre incoherentes, un acte qui reecrit ecrasant le classement tout
    #: juste fige.
    #:
    #: Regle du depot : « Result est fige au reveal, jamais recalcule : il
    #: devient l'input d'une autre activite et l'historique doit rester
    #: stable. » C'est pour cela que `revealed_payload` RELIT le `Result` fige
    #: au lieu de reagreger des qu'une activite declare ce point d'accroche.
    freeze_results: Callable[[list], dict] | None = field(default=None)

    #: La valeur que le facilitateur retient a `result.act` est-elle recevable ?
    #: Signature : (chosen_value, card_values) -> bool.
    #:
    #: Defaut (`_default_validate_chosen_value`) : elle appartient au deck actif
    #: -- la regle du poker, inchangee, deplacee telle quelle depuis
    #: `services.act_result`. C'est elle qui rendait `act_result` INERTE pour
    #: toute activite sans cartes : `card_values` y est toujours vide (design
    #: §7), donc la garde refusait TOUT, y compris la seule valeur
    #: sensee. Pas « pas encore branchee » : inerte.
    validate_chosen_value: Callable[[object, list[str]], bool] = field(default=None)

    #: Comment un `Result` de cette activite se rend dans l'HISTORIQUE d'equipe
    #: (`history/api_views.py`, et le compte rendu envoye aux managers).
    #: Signature : (result, label_for) -> dict, fusionne dans l'entree du jour.
    #: `label_for(value)` resout une valeur de carte en son libelle traduit
    #: depuis le snapshot de deck du round -- une fonction, et non le snapshot
    #: lui-meme, pour que le registre n'ait pas a connaitre la forme d'un
    #: snapshot.
    #:
    #: Defaut (`_default_history_entry`) : `{"chosenValue", "levelName"}`,
    #: rigoureusement l'entree que `history` construisait en dur.
    #:
    #: Vit dans le registre parce que l'historique etait le DERNIER lecteur
    #: faconne pour le poker : il rend la valeur retenue comme le LIBELLE D'UNE
    #: CARTE. Un total de Dot Voting rendu par ce chemin s'afficherait comme un
    #: niveau de delegation -- dans un compte rendu envoye a des managers, ce
    #: n'est pas un defaut d'affichage, c'est un enregistrement FAUX.
    history_entry: Callable[[object, Callable[[str], object]], dict] = field(default=None)

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

    #: Ce qu'il reste a placer, pour UN participant sur CE round -- diffuse
    #: au FACILITATEUR SEUL, filtre a l'emission (jamais un masquage cote
    #: client) : design dot voting §4, brief tache 6a-5. Les jetons n'etant
    #: pas obligatoires, "a fini" cesse d'etre deductible du seul nombre de
    #: reponses -- le facilitateur a besoin de cette quantite pour savoir qui
    #: reflechit encore.
    #:
    #: Signature : (existing, item_count) -> objet JSON-serialisable (un
    #: entier pour dot_voting_v1). `existing` : les reponses [(item_id,
    #: payload), ...] DEJA ECRITES par CE participant sur ce round -- MEME
    #: forme que `validate_responses` juste au-dessus (memes tuples, pas de
    #: second format a maintenir).
    #:
    #: `None` (defaut, le poker) : cette activite n'a aucune notion de budget
    #: -- `services.remaining_budgets` renvoie `None` et rien n'est diffuse.
    #: Ne PAS confondre avec `validate_responses`, qui juge un BOOLEEN (la
    #: tentative passe-t-elle) ; celle-ci rend le RESTE, une quantite a
    #: afficher, pas une decision a appliquer.
    remaining_budget: Callable[[list, int], object] | None = field(default=None)

    def __post_init__(self):
        if self.aggregate is None:
            object.__setattr__(self, "aggregate", _default_aggregate(self.ordinal))
        if self.validate_value is None:
            object.__setattr__(self, "validate_value", _default_validate_value)
        if self.validate_responses is None:
            object.__setattr__(self, "validate_responses", _default_validate_responses)
        if self.response_view is None:
            object.__setattr__(self, "response_view", _default_response_view)
        if self.validate_chosen_value is None:
            object.__setattr__(self, "validate_chosen_value", _default_validate_chosen_value)
        if self.history_entry is None:
            object.__setattr__(self, "history_entry", _default_history_entry)


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


def _default_response_view(payload):
    """La forme historique du poker : le depouillement nominatif emet
    `{"participantId": ..., "cardValue": ...}`. Lue telle quelle par
    `Facilitation_frontend` -- `cardValue` est la cle que le contrat fige pour
    `itemResults[].votes` (§8.2.a), d'ou un defaut rigoureusement identique au
    code en dur qu'il remplace."""
    return {"cardValue": payload.get("card")}


def _default_validate_chosen_value(chosen_value, card_values):
    """La regle du poker : le facilitateur retient une carte du deck actif.
    Exactement le test qui vivait en dur dans `services.act_result`, deplace
    sans changer d'un caractere -- `chosen_value not in _card_values(room)`
    refusait, cette fonction accepte l'inverse."""
    return chosen_value in card_values


def _default_history_entry(result, label_for):
    """L'entree d'historique du poker : la valeur retenue, et le NOM traduit
    de la carte correspondante. Exactement ce que `history._entries_for`
    construisait en dur -- meme cles, meme contenu, meme repli sur la valeur
    brute pour une carte inconnue (c'est `label_for` qui porte ce repli)."""
    return {"chosenValue": result.chosen_value, "levelName": label_for(result.chosen_value)}


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
    n sur un meme item -- d'ou 0 <= points <= n (design §2-3, brief
    tache 6a-2). La contrainte GLOBALE (la somme d'un participant sur tout
    le round <= 2n) n'est PAS verifiee ici : elle porte sur l'ensemble des
    reponses d'un participant, pas sur une reponse a la fois, et c'est
    precisement l'ouverture de domaine que la tache suivante ajoute (design
    §3, point 3).
    """
    points = payload.get("points")
    if isinstance(points, bool) or not isinstance(points, int):
        return False
    return 0 <= points <= item_count


def _dot_voting_validate_responses(existing, item_id, payload, item_count=0):
    """La contrainte GLOBALE que `_dot_voting_validate_value` annoncait ne
    PAS verifier (design §3, point 3 ; tache 6a-3) : la somme des
    jetons qu'UN participant pose sur TOUT le round ne doit pas depasser son
    budget de 2n jetons (n = item_count, design §1).

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
    §1 et §6) -- la meme fonction que reutilisera weighted_dot_voting
    (tache 6b), les deux activites partageant le depouillement (design
    §1 : « elles partagent l'agregation, le classement et le
    rendu »).

    Forme volontairement differente du defaut poker (`{"tally": [...],
    "spread": {...}}`) : il n'y a ici ni carte a denombrer ni ecart ordinal,
    seulement un total. `card_values` est accepte pour respecter la
    signature commune a tout `aggregate` du registre, mais reste inutilise
    -- toujours vide pour cette strategie (design §7).

    Cette forme differente est desormais DIFFUSABLE telle quelle : depuis la
    tache 6a-4, `revealed_payload` (`realtime/services.py`) fusionne ce que
    rend `aggregate` dans le bloc de l'item (`{"itemId": ..., **counted}`)
    au lieu d'y piocher `counted["tally"]` / `counted["spread"]` en dur --
    ce qui levait une `KeyError` immediate sur tout agregat non-poker.
    """
    total = sum(r.payload.get("points", 0) for r in responses)
    return {"totalPoints": total, "responseCount": len(responses)}


def _dot_voting_response_view(payload):
    """Depouillement nominatif : combien de jetons CE participant a pose sur
    cet item. Pas de `cardValue` -- cette activite n'a pas de cartes (design
    §7), et le defaut poker aurait emis `cardValue: null`.

    N'est appele QUE sur un round non anonyme : c'est `revealed_payload` qui
    tient l'invariant, en ne construisant rien de nominatif autrement. Voir
    `ActivitySpec.response_view`."""
    return {"points": payload.get("points", 0)}


def _dot_voting_freeze_results(aggregates):
    """Le classement fige a la revelation (design §6) : somme des points
    par item, du plus haut au plus bas.

    `aggregates` arrive dans l'ordre (sequence, id) des items du round, et
    `sorted` est STABLE en Python (garanti par le langage) : deux items a
    egalite de points ressortent donc toujours dans l'ordre de sequence du
    round, jamais dans un ordre arbitraire. C'est EXACTEMENT le departage
    retenu par la tache 6a-2 pour `_dot_voting_rank_value` (voir sa
    docstring) -- le meme, pas un second : un classement fige ici et un
    classement recalcule au chainage doivent trancher les ex aequo de la
    meme facon, sinon le « top N » ne reprendrait pas les items que le
    depouillement a montres.

    Les rangs sont des POSITIONS strictes (1, 2, 3...), pas des rangs
    partages : deux items a egalite recoivent deux rangs consecutifs, celui
    de plus petite sequence d'abord. Un rang partage ne dirait pas au
    facilitateur lequel de deux ex aequo un « top 1 » emporterait, alors que
    la reponse, elle, est deterministe.

    `chosenValue` porte le TOTAL, en chaine : c'est le champ que
    `_dot_voting_rank_value` relit (`int(result.chosen_value)`) pour le
    chainage « top N », et le seul que `Result` portait avant cette tache.
    Le detail (total + rang) va dans `payload`, le champ additif ajoute par
    cette tache.
    """
    ordered = sorted(aggregates, key=lambda pair: pair[1].get("totalPoints", 0), reverse=True)
    return {
        item_id: {
            "chosenValue": str(counted.get("totalPoints", 0)),
            "payload": dict(counted, rank=position),
        }
        for position, (item_id, counted) in enumerate(ordered, start=1)
    }


def _dot_voting_validate_chosen_value(chosen_value, card_values):
    """Acter un round de Dot Voting ne retient AUCUNE valeur : le classement
    est deja fige a la revelation (`_dot_voting_freeze_results`), l'acte ne
    fait que conclure le round. La regle par defaut (« la valeur appartient
    au deck ») rendait cet acte impossible : `card_values` est toujours vide
    pour une activite sans cartes, donc elle refusait tout.

    Accepte donc l'absence de valeur (`None`, ce que le consumer transmet
    quand le client n'envoie pas de `chosenValue`, et `""`), et refuse une
    valeur porteuse : la laisser passer ecrirait dans `Result.chosen_value`
    un jeton que rien ne relit, en travers du total que la revelation vient
    d'y figer."""
    return chosen_value in (None, "")


def _dot_voting_history_entry(result, label_for):
    """L'entree d'historique de Dot Voting. `label_for` est deliberement NON
    appele : cette activite n'a pas de cartes, donc aucun libelle de carte a
    resoudre -- le resoudre quand meme retomberait sur la valeur brute et
    afficherait un TOTAL la ou le lecteur attend un niveau, ce qui, dans un
    compte rendu envoye aux managers, est un enregistrement faux.

    `levelName` reste emis parce que c'est la cle que consomment le front ET
    le courriel (`history/email.py::_plain_level`, qui ferait litteralement
    « None » d'une valeur absente). Il porte donc une phrase qui dit ce que
    la valeur EST -- « 12 points » -- identique en francais et en anglais,
    donc sans traduction a inventer ici. Le detail chiffre part a cote
    (`totalPoints`, `rank`) pour qu'un lecteur de l'API n'ait pas a reparser
    la phrase."""
    payload = result.payload or {}
    total = payload.get("totalPoints")
    if total is None:
        total = result.chosen_value
    rank = payload.get("rank")
    label = f"{total} points"
    return {
        "chosenValue": result.chosen_value,
        "levelName": {"en": label, "fr": label},
        "totalPoints": total,
        "rank": rank,
    }


def _dot_voting_remaining_budget(existing, item_count):
    """Ce qu'il reste a placer (design §1, §4 ; brief tache 6a-5) : le
    budget total 2n moins ce que CE participant a DEJA pose, tous items du
    round confondus -- pas un booleen comme `_dot_voting_validate_responses`
    (qui juge une somme CONTRE 2n), juste sa moitie utile ici : le reste, une
    quantite a afficher au facilitateur, pas une decision a appliquer.

    `existing` porte deja l'etat COURANT (post-remplacement, cf.
    `cast_response`) au moment ou `services.remaining_budgets` lit la table --
    aucun piege de double-compte a reproduire ici, contrairement a
    `_dot_voting_validate_responses` qui doit lui-meme ECRASER l'entree de la
    tentative EN COURS avant de sommer."""
    return 2 * item_count - sum(p.get("points", 0) for _, p in existing)


def _dot_voting_rank_value(result):
    """Classement (design §6) : le total de l'item, PLUS GRAND = PLUS
    prioritaire -- donc le total lui-meme, aucune transformation.

    Lit `Result.chosen_value` (CharField) comme une chaine d'entier.
    Depuis la tache 6a-4, ce champ est bien ALIMENTE en production :
    `_dot_voting_freeze_results` y ecrit le total de l'item a la revelation,
    et `Result.payload` (migration `0022_result_payload`) porte a cote le
    detail (total + rang). `rank_value` continue de lire `chosen_value`
    plutot que `payload["rank"]` : un rang se deduit du total, l'inverse
    non, et `bind_round` fige une strategie SOURCE dont le classement doit
    rester calculable sur un `Result` venu de n'importe quelle activite.

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
    # (somme <= 2n, remplacement compris -- design §3, point 3).
    # `consumes="items"` :
    # l'activite distribue des jetons sur des items existants, saisis ou
    # copies d'une source chainee. `produces="results"` + `rank_value` :
    # chainable, et c'est elle qui allume le "top N" du chainage (design
    # §6), reste inutilisable depuis 5e faute d'une activite sachant
    # classer. `config_schema` : {"liveTotals": bool} -- le facilitateur
    # choisit si les TOTAUX (jamais le lien participant -> jetons) sont
    # visibles pendant le vote (design §5). Absente de `Round.config`
    # (valeur par defaut du modele : {}) tant que le facilitateur n'a rien
    # choisi -- a lire cote domaine avec `.get("liveTotals", False)`, secret
    # par defaut, jamais un oubli de configuration.
    #
    # Tache 6a-4 : `response_view`, `freeze_results` et `validate_chosen_value`
    # completent l'entree pour que le DEPOUILLEMENT et l'ACTE cessent d'etre
    # poker-specifiques cote domaine. Le classement se fige A LA REVELATION
    # (design §6) dans `Result.chosen_value` + `Result.payload`
    # (migration `0022_result_payload`), et `result.act` ne fait plus alors
    # que conclure le round.
    #
    # Tache 6a-5 : `remaining_budget` alimente `services.remaining_budgets`,
    # diffuse au FACILITATEUR SEUL (design §4) par
    # `realtime/consumers.py::_dispatch` apres chaque `response.cast` --
    # jamais au votant, filtre a l'emission, jamais un masquage cote client.
    # `liveTotals` (config_schema ci-dessus) alimente desormais
    # `services.live_totals_payload`, diffuse A TOUS mais seulement si le
    # round est `open` ET que sa config l'autorise (design §5) -- toujours un
    # AGREGAT, jamais un lien participant -> jetons (contrat §8.7).
    "dot_voting_v1": ActivitySpec(
        payload_schema={"points": int},
        config_schema={"liveTotals": bool},
        validate_value=_dot_voting_validate_value,
        validate_responses=_dot_voting_validate_responses,
        aggregate=_dot_voting_aggregate,
        response_view=_dot_voting_response_view,
        freeze_results=_dot_voting_freeze_results,
        validate_chosen_value=_dot_voting_validate_chosen_value,
        history_entry=_dot_voting_history_entry,
        rank_value=_dot_voting_rank_value,
        remaining_budget=_dot_voting_remaining_budget,
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
