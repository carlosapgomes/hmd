"""Comando ``reenqueue_pending_reappearances`` — recuperação técnica (slice 002, D6).

Seleciona UM vínculo de reapresentação pelo ID explícito e agenda SOMENTE o
efeito pendente elegível (cluster ``llm``), com auditoria no incoming. Nunca
executa resolução/LLM, nunca toma lease, nunca zera orçamento nem reativa
terminal/aplicado/stale.

Uso:
    uv run python manage.py reenqueue_pending_reappearances <id-do-vinculo> --settings=config.settings.dev
    uv run python manage.py reenqueue_pending_reappearances <id-do-vinculo> --apply --settings=config.settings.dev

Modo padrão é SIMULAÇÃO (zero escritas). ``--apply`` executa.

Saída/exits: ``0`` simulação válida (inclusive diagnóstico de
inelegibilidade), agendamento efetivo ou NO-OP com agenda válida existente;
``1`` recusa em ``--apply``, vínculo inexistente ou falha de persistência;
``2`` uso inválido (argumento/UUID malformado — erro do parser).
"""

from __future__ import annotations

import logging
import uuid

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cases.models import CaseReappearance
from apps.cases.reappearance import apply_reenqueue, evaluate_reenqueue

_logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Reenfileira a recuperação técnica de UM vínculo de reapresentação "
        "pendente (simulação por padrão; --apply agenda o efeito)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "link_id",
            type=uuid.UUID,
            help="ID (UUID) do vínculo CaseReappearance a reenfileirar.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Agenda de fato o efeito pendente (sem --apply, só simula).",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        link_id: uuid.UUID = options["link_id"]  # type: ignore[assignment]
        apply = bool(options["apply"])
        try:
            link = CaseReappearance.objects.get(pk=link_id)
        except CaseReappearance.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"RECUSADO: vínculo {link_id} não encontrado."))
            raise CommandError(f"vínculo {link_id} não encontrado") from None

        if not apply:
            outcome = evaluate_reenqueue(link)
            detail = f"motivo={outcome.reason_code} tentativa={outcome.attempt_count}"
            if outcome.reason_code == "valid_schedule_exists":
                self.stdout.write(
                    self.style.SUCCESS(
                        f"SIMULAÇÃO: vínculo {link_id} com agenda válida "
                        f"existente (NO-OP) — {detail}. Nenhuma escrita."
                    )
                )
            else:
                self.stdout.write(f"SIMULAÇÃO: vínculo {link_id} — {detail}. Nenhuma escrita.")
            return

        try:
            outcome = apply_reenqueue(link_id)
        except Exception:
            _logger.warning("reappearance reenqueue falhou vinculo=%s", link_id, exc_info=True)
            self.stdout.write(
                self.style.ERROR(
                    f"RECUSADO: falha de persistência ao reenfileirar o vínculo "
                    f"{link_id}; nenhuma agenda foi persistida."
                )
            )
            raise CommandError(
                f"falha de persistência ao reenfileirar o vínculo {link_id}"
            ) from None

        if outcome.status == "scheduled":
            self.stdout.write(
                self.style.SUCCESS(
                    f"AGENDADO: vínculo {link_id} — "
                    f"tentativa={outcome.attempt_count} "
                    f"motivo={outcome.reason_code}."
                )
            )
            return
        if outcome.status == "noop":
            self.stdout.write(
                self.style.SUCCESS(
                    f"NO-OP: vínculo {link_id} já possui agenda válida — "
                    f"tentativa={outcome.attempt_count} "
                    f"motivo={outcome.reason_code}. Nenhuma escrita."
                )
            )
            return
        self.stdout.write(
            self.style.ERROR(
                f"RECUSADO: vínculo {link_id} — motivo={outcome.reason_code} "
                f"tentativa={outcome.attempt_count}. Nenhuma escrita."
            )
        )
        raise CommandError(f"reenqueue recusado para o vínculo {link_id}: {outcome.reason_code}")
