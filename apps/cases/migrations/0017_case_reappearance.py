"""Vínculo e marco de reapresentação (change reapresentacao-casos-filas, slice 002, D2).

Migration ÚNICA e aditiva do slice: ``Case.reappearance_wait_started_at``
(nullable, sem backfill — nulo = legado, com fallback ao ``created_at``) e o
model ``CaseReappearance`` (vínculo incoming→prior com digest de revisão,
código de evidência, estado, kind de resolução e campos de recuperação do
retry). Unicidade do par+revisão+kind, UMA resolução de fila aplicada por
incoming (parcial, coexistindo com ``prior_context``) e check
incoming≠prior. Sem conversão de legados, sem estados FSM novos.
"""

from django.conf import settings
from django.db import migrations, models

import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("cases", "0016_case_patient_cns"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="case",
            name="reappearance_wait_started_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="CaseReappearance",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("revision_digest", models.CharField(max_length=80)),
                ("evidence_code", models.CharField(max_length=24)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pendente"),
                            ("confirmed", "Confirmada"),
                            ("independent", "Independente"),
                            ("applied", "Aplicada"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "resolution_kind",
                    models.CharField(
                        choices=[
                            ("medical_replacement", "Substituição médica"),
                            ("scheduling_repeat", "Repetição no agendamento"),
                            ("prior_context", "Contexto de caso anterior"),
                        ],
                        max_length=24,
                    ),
                ),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("first_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_reason_code",
                    models.CharField(blank=True, default="", max_length=40),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "canonical_case",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.PROTECT,
                        related_name="reappearance_canonical_links",
                        to="cases.case",
                    ),
                ),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="reappearance_confirmations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "incoming_case",
                    models.ForeignKey(
                        on_delete=models.deletion.PROTECT,
                        related_name="reappearance_incoming_links",
                        to="cases.case",
                    ),
                ),
                (
                    "prior_case",
                    models.ForeignKey(
                        on_delete=models.deletion.PROTECT,
                        related_name="reappearance_prior_links",
                        to="cases.case",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=[
                            "incoming_case",
                            "prior_case",
                            "revision_digest",
                            "resolution_kind",
                        ],
                        name="uniq_reappearance_pair_revision_kind",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(
                            state="applied",
                            resolution_kind__in=[
                                "medical_replacement",
                                "scheduling_repeat",
                            ],
                        ),
                        fields=["incoming_case"],
                        name="uniq_reappearance_applied_queue_per_incoming",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(incoming_case=models.F("prior_case")),
                        name="reappearance_incoming_ne_prior",
                    ),
                ],
            },
        ),
    ]
