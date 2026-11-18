"""Comando ``purge_closed_case_data`` — purga retroativa dos dados retidos (slice 003, D4).

Comando FINO (só orquestra): seleciona SOMENTE casos ``CLEANED`` COM dados
retidos (``case_has_retained_data`` — ≥1 dos 7 campos clínicos/LLM não-vazio
ou rows de documento/anexo) e delega cada caso ao serviço puro
``purge_closed_case_data`` (``apps/cases/closure.py``) — o serviço detém o
atomic por caso, a semântica minimizante e o evento auditável; o comando
detém seleção, laço, saída e exit code. Re-execução é no-op (caso sem dados
retidos é pulado, sem evento). Saída e payload SEM PHI: apenas referência
humana (``display_reference``) e contagens — nunca conteúdo clínico nem
nomes de arquivo. Caso com ``files_failed > 0`` é sinalizado como purga
INCOMPLETA e o comando encerra com erro (``CommandError`` = exit não-zero)
listando os casos — a falha TEM que aparecer na saída/log porque a
re-execução não redescobre o caso pelo critério de seleção (D4).

Uso:
    uv run python manage.py purge_closed_case_data --settings=config.settings.dev
    uv run python manage.py purge_closed_case_data --dry-run --settings=config.settings.dev
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cases.closure import case_has_retained_data, purge_closed_case_data, retained_data_counts
from apps.cases.models import Case, CaseStatus


class Command(BaseCommand):
    help = (
        "Minimiza retroativamente os casos CLEANED retidos (campos, rows e "
        "arquivos), registrando um evento auditável por caso. --dry-run só reporta."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista o que seria purgado (referência + contagens) sem alterar nada.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        dry_run = bool(options["dry_run"])
        purged = 0
        skipped = 0
        total_attempted = 0
        total_deleted = 0
        total_failed = 0
        incomplete: list[str] = []

        candidates = Case.objects.filter(status=CaseStatus.CLEANED).order_by(
            "created_at", "case_id"
        )
        for case in candidates:
            if not case_has_retained_data(case):
                skipped += 1
                continue
            reference = case.display_reference
            if dry_run:
                counts = retained_data_counts(case)
                self.stdout.write(
                    f"  Dry-run: {reference} seria purgado "
                    f"(campos: {len(counts.cleaned_fields)}, documentos: {counts.documents}, "
                    f"anexos: {counts.attachments}, arquivos: {counts.files})"
                )
                purged += 1
                total_attempted += counts.files
                continue

            result = purge_closed_case_data(case)
            purged += 1
            total_attempted += result.files_attempted
            total_deleted += result.files_deleted
            total_failed += result.files_failed
            if result.files_failed > 0:
                incomplete.append(reference)
                self.stdout.write(
                    self.style.ERROR(
                        f"  Purga INCOMPLETA: {reference} "
                        f"(campos: {len(result.cleaned_fields)}, "
                        f"documentos: {result.documents_deleted}, "
                        f"anexos: {result.attachments_deleted}, arquivos: "
                        f"{result.files_attempted} tentados, "
                        f"{result.files_deleted} removidos, "
                        f"{result.files_failed} falhados)"
                    )
                )
            else:
                self.stdout.write(
                    f"  Purgado: {reference} "
                    f"(campos: {len(result.cleaned_fields)}, "
                    f"documentos: {result.documents_deleted}, "
                    f"anexos: {result.attachments_deleted}, arquivos: "
                    f"{result.files_attempted} tentados, "
                    f"{result.files_deleted} removidos, "
                    f"{result.files_failed} falhados)"
                )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"purge_closed_case_data (dry-run) concluído: {purged} seriam purgados, "
                    f"{skipped} pulados, arquivos: {total_attempted} tentados. "
                    "Nenhum dado alterado."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"purge_closed_case_data concluído: {purged} purgados, "
                    f"{skipped} pulados, arquivos: {total_attempted} tentados, "
                    f"{total_deleted} removidos, {total_failed} falhados."
                )
            )

        if incomplete:
            raise CommandError(
                f"purga incompleta em {len(incomplete)} caso(s): {', '.join(incomplete)}"
            )
