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
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActivitySpec:
    """Ce qu'une strategie de resolution dit du depouillement."""

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


def is_ordinal(strategy: str | None) -> bool:
    """Vrai si un ecart min/max a un sens sur cette echelle."""
    return spec_for(strategy).ordinal
