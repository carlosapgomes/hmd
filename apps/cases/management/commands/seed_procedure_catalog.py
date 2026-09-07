"""Comando ``seed_procedure_catalog`` — verificação idempotente (change 03, slice 001, R6).

O catálogo vive em código (``apps.cases.procedure_catalog``); este comando não
grava nada — valida a coerência do registro (13 tipos, sem duplicatas, seções
S1–S8 existentes, subtipos válidos, seção↔tipos coerente) e imprime um resumo.
Executável quantas vezes for preciso: é idempotente por natureza.

Uso:
    uv run python manage.py seed_procedure_catalog --settings=config.settings.dev
"""

from django.core.management.base import BaseCommand, CommandError

from apps.cases.procedure_catalog import CRITERIA_SECTIONS, PROCEDURE_PROFILES, catalog_problems


class Command(BaseCommand):
    help = "Valida a coerência do catálogo de procedimentos (13 tipos, S1–S8) e imprime resumo"

    def handle(self, *args: object, **options: object) -> None:
        problems = catalog_problems()
        if problems:
            raise CommandError("Catálogo de procedimentos inconsistente: " + "; ".join(problems))

        self.stdout.write(
            f"Catálogo de procedimentos: {len(PROCEDURE_PROFILES)} perfis, "
            f"{len(CRITERIA_SECTIONS)} seções de critérios (S1–S8), sem duplicatas."
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Verificação concluída: registro íntegro (idempotente — pode rodar 2× sem efeito)."
            )
        )
