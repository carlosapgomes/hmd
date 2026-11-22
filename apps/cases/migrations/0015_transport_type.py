"""Tipo de transporte solicitado (change transport-badge, slice 001, R2).

Migration ÚNICA e aditiva do slice: ``transport_type`` nulável (CharField com
choices ``basic``/``mobile_icu``). Sem backfill (design D5): casos já
processados ficam com o campo vazio (estado legítimo); reprocessamentos
futuros populam.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0014_doctor_decided_stamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="transport_type",
            field=models.CharField(
                blank=True,
                choices=[("basic", "Ambulância Básica"), ("mobile_icu", "UTI Móvel")],
                max_length=16,
                null=True,
            ),
        ),
    ]
