"""Metadados do cabeçalho SESAB no Case (change sesab-header-extraction, D3/D6).

Migration ÚNICA do slice 001 para os 4 campos extraídos do cabeçalho padrão
SESAB (repetido por página) pelo worker pdf: ``patient_age`` e
``days_on_screen`` (PositiveSmallIntegerField, null) e ``patient_gender``/
``patient_race`` (CharField blank). Todos opcionais/vazios — casos existentes
seguem válidos sem migração de dados. Os campos SOBREVIVEM ao CLEANED por
paridade com ``agency_record_number`` (histórico administrativo do caso); são
zerados apenas no reenvio de documentos (``_RESUBMIT_CLEARED_FIELDS``).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0009_case_correction"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="days_on_screen",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_age",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_gender",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_race",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
