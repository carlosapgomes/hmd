"""Carimbos de decisão de agendamento (slice 004 da change paridade-ats, D5).

Migration ÚNICA e aditiva do slice: ``scheduling_decided_at`` +
``scheduling_decided_by`` (FK SET_NULL) com backfill auditável pela trilha —
o ÚLTIMO evento ``CASE_STATUS_SCHEDULING_CONFIRMED|DENIED`` por caso vira o
par timestamp+ator; sem evento correspondente (ou ator nulo), os campos ficam
nulos (fail-closed: o caso fica fora do universo do histórico, sem afetar as
abas «Hoje» de ninguém).
"""

from __future__ import annotations

from typing import Any

import django.db.models.deletion
from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def backfill_scheduling_decided(apps: Apps, schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    """Backfill dos carimbos de decisão de agendamento (D5).

    Último ``CASE_STATUS_SCHEDULING_CONFIRMED|DENIED`` por caso (ordem de
    ``timestamp`` com desempate por ``id``) → ``scheduling_decided_at`` =
    timestamp do evento, ``scheduling_decided_by`` = ator; evento sem ator
    (nulo) ou caso sem evento ⇒ campos nulos (fail-closed). Idempotente (só
    preenche onde o carimbo ainda é nulo).
    """
    del schema_editor
    case_model = apps.get_model("cases", "Case")
    event_model = apps.get_model("cases", "CaseEvent")
    decision_types = (
        "CASE_STATUS_SCHEDULING_CONFIRMED",
        "CASE_STATUS_SCHEDULING_DENIED",
    )
    latest_by_case: dict[Any, Any] = {}
    events: Any = (
        event_model.objects.filter(event_type__in=decision_types)
        .order_by("case_id", "timestamp", "id")
        .only("case_id", "timestamp", "actor_id")
    )
    for event in events:
        latest_by_case[event.case_id] = event
    cases: Any = case_model.objects
    for case_id, event in latest_by_case.items():
        actor_id = event.actor_id
        if actor_id is None:
            continue
        cases.filter(
            pk=case_id,
            scheduling_decided_at__isnull=True,
        ).update(
            scheduling_decided_at=event.timestamp,
            scheduling_decided_by_id=actor_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0012_post_closure_issue"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="scheduling_decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="case",
            name="scheduling_decided_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="cases_scheduling_decided",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            backfill_scheduling_decided,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
