"""Campos do reenvio corrigido no Case (change nir-result-closure, D4).

Migration ÚNICA do slice 004 para os 3 campos do vínculo de reenvio
corrigido: ``corrects_case`` (self-FK SET_NULL, null, com o reverso
``corrected_by``), ``correction_reason`` (TextField blank) e
``correction_created_by`` (FK do autor do reenvio, SET_NULL, null). Todos
opcionais/blank — casos existentes seguem válidos sem migração de dados.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0008_case_scheduling"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="corrects_case",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="corrected_by",
                to="cases.case",
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="correction_created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cases_corrections_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="correction_reason",
            field=models.TextField(blank=True),
        ),
    ]
