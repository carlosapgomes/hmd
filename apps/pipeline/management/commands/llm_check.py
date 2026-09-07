"""Comando manual ``llm_check`` (change llm-pipeline-per-type, slice 001, R5).

Para cada modelo solicitado (``--models l1,l2``, default ambos), executa **uma**
chamada mínima de conectividade ("Responda: ok") via cliente e reporta
modelo/resultado/latência — ou o ``LlmError.kind`` + mensagem em falha. Sem
banco de casos: é o aceite operacional de chave/URL/modelos da OpenRouter,
**fora do CI** (a suíte usa factory fake e nunca toca a rede).

Modelo de estágio não configurado (``LLM1_MODEL``/``LLM2_MODEL`` vazios) é
reportado como ``modelo_nao_configurado`` e conta como falha; exit ≠ 0 se
qualquer checagem falhar.

Uso:
    uv run python manage.py llm_check                      # l1 e l2
    uv run python manage.py llm_check --models l1          # só o LLM1
    LLM1_MODEL=... OPENROUTER_API_KEY=... uv run python manage.py llm_check
"""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.pipeline.llm import LlmClient, LlmError, get_llm_client

# Aliases aceitos por ``--models`` → settings do modelo de cada estágio.
MODEL_SETTING_BY_ALIAS: dict[str, str] = {
    "l1": "LLM1_MODEL",
    "l2": "LLM2_MODEL",
}
# Chamada mínima de conectividade (R5) — sem payload/PII.
PROBE_MESSAGE = "Responda: ok"


class Command(BaseCommand):
    help = (
        "Executa 1 chamada mínima por modelo OpenRouter configurado e reporta "
        "modelo/ok ou LlmError.kind/latência (diagnóstico manual, fora do CI)"
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--models",
            default="l1,l2",
            help="Aliases de estágio a checar, separados por vírgula (l1, l2). Default: l1,l2.",
        )

    def _model_for_alias(self, alias: str) -> str:
        """Devolve o modelo do alias (``LLM1_MODEL``/``LLM2_MODEL``), ou vazio."""
        setting_name = MODEL_SETTING_BY_ALIAS[alias]
        value: str = getattr(settings, setting_name, "")
        return value.strip()

    def handle(self, *args: Any, **options: Any) -> None:
        aliases = [
            alias.strip().lower() for alias in str(options["models"]).split(",") if alias.strip()
        ]
        unknown = [alias for alias in aliases if alias not in MODEL_SETTING_BY_ALIAS]
        if unknown:
            raise CommandError(
                f"Aliases desconhecidos em --models: {', '.join(unknown)} (use l1, l2)."
            )

        client: LlmClient | None = None
        failures = 0
        for alias in aliases:
            model = self._model_for_alias(alias)
            if not model:
                failures += 1
                self.stdout.write(
                    f"{alias}: result=modelo_nao_configurado "
                    f"model_setting={MODEL_SETTING_BY_ALIAS[alias]}"
                )
                continue

            if client is None:
                try:
                    client = get_llm_client()
                except LlmError as exc:
                    # Falha fechado na construção (ex.: OPENROUTER_API_KEY
                    # ausente) — conta como falha do modelo.
                    failures += 1
                    self.stdout.write(
                        f"{alias}: result=erro kind={exc.kind} message={exc} model={model}"
                    )
                    continue

            start = time.perf_counter()
            try:
                client.complete(model, [{"role": "user", "content": PROBE_MESSAGE}])
            except LlmError as exc:
                failures += 1
                elapsed_ms = (time.perf_counter() - start) * 1000
                self.stdout.write(
                    f"{alias}: result=erro kind={exc.kind} message={exc} "
                    f"model={model} latency_ms={elapsed_ms:.0f}"
                )
                continue
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.stdout.write(f"{alias}: result=ok model={model} latency_ms={elapsed_ms:.0f}")

        if failures:
            raise CommandError(
                f"llm_check: {failures} checagem(ns) falharam — modelos não "
                "configurados ou erros reportados acima."
            )
