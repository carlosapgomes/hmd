"""Comando ``purge_closed_case_data`` — purga retroativa dos dados retidos (slice 003, D4/D8).

Agrupa e deduplica unidades de disposição por ``canonical_case_id`` em ``--dry-run``
e execução real; protege explicitamente incomings ``CLEANED`` vinculados via
``scheduling_repeat`` a canônico não-``CLEANED`` (inclusive ``FINAL_REPLY_POSTED``).
Saída e payload SEM PHI: apenas referência humana (``display_reference``), estado
e contagens. Falha física por caso encerra com ``CommandError`` (exit não-zero).
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.cases.closure import (
    _CLEANED_EMPTY_VALUES,
    purge_closed_case_data,
    retained_data_counts,
)
from apps.cases.models import (
    Case,
    CaseReappearance,
    CaseStatus,
    ReappearanceState,
    ResolutionKind,
)


class Command(BaseCommand):
    help = (
        "Minimiza retroativamente os casos CLEANED retidos (campos, rows e "
        "arquivos), agrupando reapresentações de agendamento por canônico. "
        "--dry-run só reporta."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista o que seria purgado (referência + contagens) e protegidos sem alterar nada.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        dry_run = bool(options["dry_run"])
        purged = 0
        skipped = 0
        protected_count = 0
        total_attempted = 0
        total_deleted = 0
        total_failed = 0
        incomplete: list[str] = []

        cleaned_cases = list(
            Case.objects.filter(status=CaseStatus.CLEANED).order_by("created_at", "case_id")
        )
        cleaned_ids = [c.case_id for c in cleaned_cases]

        applied_links = list(
            CaseReappearance.objects.filter(
                incoming_case_id__in=cleaned_ids,
                resolution_kind=ResolutionKind.SCHEDULING_REPEAT,
                state=ReappearanceState.APPLIED,
            ).select_related("canonical_case")
        )
        link_by_incoming: dict[uuid.UUID, CaseReappearance] = {
            link.incoming_case_id: link for link in applied_links
        }

        groups: dict[uuid.UUID, tuple[Case, list[Case]]] = {}
        protected_items: list[tuple[Case, Case]] = []

        for case in cleaned_cases:
            link = link_by_incoming.get(case.case_id)
            if link is not None and link.canonical_case is not None:
                canonical = link.canonical_case
                if canonical.status != CaseStatus.CLEANED:
                    protected_items.append((case, canonical))
                    continue
                unit_id = canonical.case_id
                if unit_id not in groups:
                    groups[unit_id] = (canonical, [])
                groups[unit_id][1].append(case)
            else:
                unit_id = case.case_id
                if unit_id not in groups:
                    groups[unit_id] = (case, [])

        for incoming, canonical in protected_items:
            protected_count += 1
            prefix = "Protegido (dry-run)" if dry_run else "Protegido"
            self.stdout.write(
                f"  {prefix}: {incoming.display_reference} — vinculado a canônico ativo "
                f"{canonical.display_reference} ({canonical.status})"
            )

        for canonical, incomings in groups.values():
            members = [canonical, *incomings]
            member_counts = [retained_data_counts(m) for m in members]
            has_data = any(
                c.cleaned_fields or c.documents > 0 or c.attachments > 0 for c in member_counts
            )
            if not has_data:
                skipped += 1
                continue

            reference = canonical.display_reference
            group_note = f" (grupo: {len(members)} casos)" if len(members) > 1 else ""

            if dry_run:
                cleaned_union = {f for c in member_counts for f in c.cleaned_fields}
                fields_count = sum(1 for f in _CLEANED_EMPTY_VALUES if f in cleaned_union)
                docs_count = sum(c.documents for c in member_counts)
                atts_count = sum(c.attachments for c in member_counts)
                files_count = sum(c.files for c in member_counts)
                self.stdout.write(
                    f"  Dry-run: {reference}{group_note} seria purgado "
                    f"(campos: {fields_count}, documentos: {docs_count}, "
                    f"anexos: {atts_count}, arquivos: {files_count})"
                )
                purged += 1
                total_attempted += files_count
                continue

            try:
                result = purge_closed_case_data(canonical)
            except ValueError:
                if incomings:
                    for incoming in incomings:
                        protected_count += 1
                        self.stdout.write(
                            f"  Protegido: {incoming.display_reference} — canônico "
                            f"{reference} reaberto concorrentemente"
                        )
                else:
                    skipped += 1
                continue
            if result.protected:
                protected_count += 1
                self.stdout.write(f"  Protegido: {reference} — canônico ativo")
                continue

            purged += 1
            total_attempted += result.files_attempted
            total_deleted += result.files_deleted
            total_failed += result.files_failed
            if result.files_failed > 0:
                incomplete.append(reference)
                self.stdout.write(
                    self.style.ERROR(
                        f"  Purga INCOMPLETA: {reference}{group_note} "
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
                    f"  Purgado: {reference}{group_note} "
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
                    f"{protected_count} protegidos, {skipped} pulados, "
                    f"arquivos: {total_attempted} tentados. Nenhum dado alterado."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"purge_closed_case_data concluído: {purged} purgados, "
                    f"{protected_count} protegidos, {skipped} pulados, "
                    f"arquivos: {total_attempted} tentados, "
                    f"{total_deleted} removidos, {total_failed} falhados."
                )
            )

        if incomplete:
            raise CommandError(
                f"purga incompleta em {len(incomplete)} caso(s): {', '.join(incomplete)}"
            )
