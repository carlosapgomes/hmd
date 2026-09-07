"""Artefatos do pipeline LLM no Case (change llm-pipeline-per-type, D10/R1).

Migration ÚNICA do change para os 4 campos de artefato do pipeline:
``structured_data``/``suggested_action``/``policy_result`` (JSON com default)
e ``summary_text`` (Text vazio) — nascem no slice 004 (dono definido; os
slices 005/006 apenas usam). Todos com default/blank: casos existentes
seguem válidos sem migração de dados.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0006_case_anonymization_artifacts"),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="policy_result",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="case",
            name="structured_data",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="case",
            name="suggested_action",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="case",
            name="summary_text",
            field=models.TextField(blank=True),
        ),
    ]
