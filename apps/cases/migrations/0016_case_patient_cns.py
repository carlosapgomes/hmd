"""CNS estrutural do paciente (change reapresentacao-casos-filas, slice 001, R1).

Migration ÚNICA e aditiva do slice: ``Case.patient_cns`` (CharField de 15
dígitos, vazio por default, indexado). Sem backfill (Migration Plan): casos
já processados ficam com o campo vazio (estado legítimo — identidade CNS
ausente); reprocessamentos futuros populam pelo wrapper de anonimização.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0015_transport_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="patient_cns",
            field=models.CharField(blank=True, db_index=True, default="", max_length=15),
        ),
    ]
