"""Petits utilitaires partages par les tests synchrones du domaine temps reel.

`services.cast_vote` (la facade poker qui resolvait le premier item du round
courant avant de deleguer a `cast_response`) a ete retiree fin 5b (contrat
§8.2.b) : ce helper reproduit exactement le meme raccourci pour les tests qui
ne portent pas sur le ciblage par item et n'ont donc pas besoin de repeter
cette resolution a chaque appel.
"""
from realtime import services


def cast_first_item(room, participant, card_value):
    """Enregistre `card_value` sur le PREMIER item du round courant, comme le
    faisait `cast_vote`."""
    rnd = services.current_round(room)
    item = rnd.items.first()
    return services.cast_response(room, participant, item.id, {"card": card_value})
