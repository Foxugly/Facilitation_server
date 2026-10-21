from django.db import migrations
from django.db.models.functions import Lower


# DONNEES SEULES (pas de schema), comme le veut la regle de 0022.
# Jusqu'ici, un membre sans nom de profil entrait dans une salle d'equipe sous
# son e-mail, diffuse a toute la salle (state.sync, participant.joined).
# Decision produit du 2026-10-21 : les coequipiers ne voient que le nom.
# On renomme ces sieges en "Participant N" (N = rang du siege dans la salle),
# le meme repli que rooms/api_views.py::_display_name_for.
def forwards(apps, schema_editor):
    Participant = apps.get_model("rooms", "Participant")
    leaked = (
        Participant.objects.filter(user__isnull=False)
        .annotate(name_lc=Lower("display_name"), email_lc=Lower("user__email"))
        .values_list("id", "room_id", "name_lc", "email_lc")
    )
    to_fix = {pid: room_id for pid, room_id, name, email in leaked if name == email}
    for room_id in set(to_fix.values()):
        seats = list(Participant.objects.filter(room_id=room_id).order_by("id").values_list("id", flat=True))
        for pid in seats:
            if pid in to_fix:
                Participant.objects.filter(pk=pid).update(display_name=f"Participant {seats.index(pid) + 1}")


class Migration(migrations.Migration):

    dependencies = [
        ("rooms", "0022_result_payload"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
