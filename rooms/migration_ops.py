"""Operations de migration de donnees, sorties des fichiers de migration.

Les fichiers de `migrations/` sont figes une fois joues ; les garder minces et
mettre la logique ici la rend lisible et testable. Ces fonctions ne prennent que
le registre `apps` de la migration : elles n'importent AUCUN modele concret, sans
quoi elles casseraient des que le modele evoluerait.
"""


def subjects_to_items(apps):
    """Transvase `Subject` (porte par la room) vers `Item` (porte par le round).

    Trois cas (design 2026-09-11 §4) :

    - un subject joue une fois -> l'unique item de son round ;
    - un subject rejoue -> une COPIE d'item par round, `origin_item` pointant la
      premiere. Sans cela, reformuler le sujet du second tour reecrirait ce qui a
      ete decide au premier tour ;
    - un subject prepare mais jamais joue -> un round `idle` cree pour le porter.
      C'est le scenario prepare, tel qu'il existait deja sans le nom.
    """
    Subject = apps.get_model("rooms", "Subject")
    Round = apps.get_model("rooms", "Round")
    Item = apps.get_model("rooms", "Item")
    Result = apps.get_model("rooms", "Result")

    for subject in Subject.objects.all().order_by("room_id", "sequence", "id"):
        rounds = list(subject.rounds.all().order_by("created_at", "id"))
        if not rounds:
            rounds = [Round.objects.create(room_id=subject.room_id, subject=subject, state="idle")]
        origin = None
        for rnd in rounds:
            item = Item.objects.create(
                round=rnd, text=subject.text, sequence=1, origin_item=origin
            )
            origin = origin or item

    for result in Result.objects.all().select_related("round"):
        # Un round porte exactement un item a ce stade : celui du subject qu'il
        # jouait. `first()` suffit donc ICI, et seulement ici — des qu'un round
        # portera N items, le resultat devra suivre SON item (`result.subject`),
        # pas le premier venu. Cette migration est figee sur l'etat d'avant 5a.
        item = Item.objects.filter(round_id=result.round_id).order_by("sequence", "id").first()
        if item is None:
            # Impossible par construction (tout round joue vient d'un subject, et la
            # boucle ci-dessus lui a cree un item). Echouer bruyamment plutot que
            # d'ignorer : 0012 rend `Result.item_id` NOT NULL dix lignes plus loin et
            # ne saurait dire que « column "item_id" contains null values ».
            raise RuntimeError(
                f"Result {result.pk} : le round {result.round_id} n'a aucun item. "
                "Transvasement incomplet — 0012 echouerait en NOT NULL sans dire pourquoi."
            )
        result.item = item
        result.save(update_fields=["item"])


def votes_to_responses(apps):
    """Rattache chaque reponse a l'item de son round et transpose sa valeur de
    carte en payload (design section 3).

    Le payload est la forme commune a toutes les activites : le poker y ecrit
    {"card": "<valeur>"}, une activite a venir y ecrira sa propre structure. La
    colonne `card_value` reste remplie jusqu'en 0015 - le front de production
    l'attend encore au moment ou cette migration tourne.
    """
    Response = apps.get_model("rooms", "Response")
    Item = apps.get_model("rooms", "Item")

    for response in Response.objects.all().select_related("round"):
        item = Item.objects.filter(round_id=response.round_id).order_by("sequence", "id").first()
        if item is None:
            # Aucun item : impossible depuis 0011, mais inventer un item ici
            # fabriquerait un sujet que personne n'a jamais pose.
            continue
        response.item = item
        response.payload = {"card": response.card_value}
        response.save(update_fields=["item", "payload"])
