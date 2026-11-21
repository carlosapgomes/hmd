"""Fundação da intercorrência pós-encerramento (slice 002, D2/D3).

Migration ÚNICA e aditiva do slice: colunas compactas da issue ativa
(``post_schedule_issue_*``) + marcador persistente de disposição
(``clinical_data_disposed_at``) com backfill + índices de busca histórica
(``patient_name``, ``agency_record_number``).
"""

from __future__ import annotations

import django.db.models.deletion
from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.utils import timezone


def backfill_clinical_data_disposed_at(
    apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None
) -> None:
    """Backfill do marcador de disposição (D2, justificativa no design).

    ``CLEANED`` com ``summary_text`` vazio ⇒ ``disposed_at`` preenchido: caso
    com agendamento confirmado passou obrigatoriamente pelo pipeline (sumário
    não-vazio ao encerrar); casos administrativamente fechados antes do
    pipeline têm sumário vazio mas nunca tiveram agendamento (inelegíveis por
    outra condição). Casos retidos (sumário presente) e não-encerrados ficam
    intactos. Idempotente (só preenche onde é nulo).
    """
    del schema_editor
    case_model = apps.get_model("cases", "Case")
    now = timezone.now()
    case_model.objects.filter(
        status="CLEANED",
        summary_text="",
        clinical_data_disposed_at__isnull=True,
    ).update(clinical_data_disposed_at=now)


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0011_case_origin_unit"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="post_schedule_issue_status",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="case",
            name="post_schedule_issue_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="case",
            name="post_schedule_issue_opened_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cases_post_closure_issue_opened",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="case",
            name="post_schedule_issue_opened_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="clinical_data_disposed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="case",
            name="agency_record_number",
            field=models.CharField(blank=True, db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name="case",
            name="patient_name",
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        migrations.RunPython(
            backfill_clinical_data_disposed_at,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
