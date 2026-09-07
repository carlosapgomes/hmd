"""Campos de artefatos de anonimização no Case (slice 003, change
presidio-anonymization, design D6/R4).

``anonymized_text``/``pseudonym_map``/``anonymization_report`` + linkage
``patient_name``/``patient_birth_date`` — persistidos pelo serviço
``apps/anonymization/services.py`` dentro do wrapper ``anonymize_case_text``.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0005_case_extracted_text_case_manual_review_reason_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="anonymization_report",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="case",
            name="anonymized_text",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_birth_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="patient_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="case",
            name="pseudonym_map",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
