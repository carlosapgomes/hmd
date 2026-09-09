"""Comando ``seed_prompts`` — seed idempotente dos prompts versionados (slice 003, R2).

Garante exatamente UMA versão ativa por nome (2 systems neutros + 26 users
por tipo): quando não há ativo, cria uma nova versão **ativa** (v1 se não
houver histórico; ``max(version)+1`` se houver); quando já há ativo, é
no-op — nunca reativa versão antiga, nunca sobrescreve conteúdo editado e
nunca apaga histórico (linhas/versões preservadas para auditoria/rollback).
Reexecutar não duplica (contagem = len(PROMPT_SEED_CONTENTS), hoje 29: 2 system + 26 user por perfil + 1 attachment).

Uso:
    uv run python manage.py seed_prompts --settings=config.settings.dev
"""

from django.core.management.base import BaseCommand

from apps.llm.models import PromptTemplate
from apps.llm.prompts_seed import PROMPT_SEED_CONTENTS


class Command(BaseCommand):
    help = (
        "Seed idempotente dos prompts LLM do HMD (29 templates: 2 system + 26 user + 1 attachment)"
    )

    def handle(self, *args: object, **options: object) -> None:
        created_count = 0
        skipped_count = 0

        for name, content in PROMPT_SEED_CONTENTS.items():
            if PromptTemplate.objects.filter(name=name, is_active=True).exists():
                skipped_count += 1
                continue
            latest = PromptTemplate.objects.filter(name=name).order_by("-version").first()
            version = (latest.version + 1) if latest is not None else 1
            PromptTemplate.objects.create(name=name, version=version, content=content)
            created_count += 1
            self.stdout.write(f"  Criado: {name} v{version}")

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_prompts concluído: {created_count} criados, "
                f"{skipped_count} já ativos (no-op)."
            )
        )
