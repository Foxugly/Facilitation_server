from rest_framework import serializers


class CreateRoomSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    # Anonymous rooms: username required. Team rooms: derived from the authed user.
    username = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    team = serializers.IntegerField(required=False, allow_null=True)
    # Rien d'autre : une salle sans compte ne choisit ni son type de poker (il se
    # change en salle) ni son dos (impose). Une salle d'equipe prend les decks
    # actives par l'equipe. Un client d'une version anterieure peut encore envoyer
    # deck_ids / card_back_id : les champs n'existant plus, ils sont ignores.


class JoinRoomSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
