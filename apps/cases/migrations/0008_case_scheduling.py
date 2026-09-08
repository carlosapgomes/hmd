"""Campos de agendamento no Case (change scheduler-multi-unit, D1).

Migration ÚNICA do change para os 7 campos da decisão do agendador:
``scheduled_unit`` (choices 1|2), ``scheduled_datetime`` (aware, exibido no
fuso local), ``scheduled_location``, ``scheduled_by`` (FK SET_NULL,
related_name ``cases_scheduled``), ``scheduled_decided_at``,
``scheduling_denial_reason`` e ``scheduling_reopen_reason`` (este último só
consumido pela intercorrência do slice 002). Todos opcionais — casos
existentes seguem válidos sem migração de dados.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0007_llm_artifacts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="scheduled_datetime",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduled_decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduled_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cases_scheduled",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduled_location",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduled_unit",
            field=models.PositiveSmallIntegerField(
                blank=True, choices=[(1, "Unidade 1"), (2, "Unidade 2")], null=True
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduling_denial_reason",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduling_reopen_reason",
            field=models.TextField(blank=True),
        ),
    ]
